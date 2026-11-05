"""Experimental native Safari DOM and network sessions, without page scripts."""
import asyncio
import base64
from collections import OrderedDict, deque
import contextlib
from dataclasses import dataclass, field
import fnmatch
import json
import time
import uuid

from .inspector_transport import InspectorError, InspectorTransport


class DocumentChangedError(InspectorError):
    """The page changed during a read; only condition waiters poll a fresh document."""


def _timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 300:
        raise ValueError('timeout must be between 0 and 300 seconds')
    return value


def _direct_inspector(service, receive, disconnected):
    from pymobiledevice3.services.webinspector import WebinspectorService

    class DirectInspector(WebinspectorService):
        # This adapter is tested against the pinned upstream revision. Its service
        # is already open; no Lockdown pairing, automation session, or retry occurs.
        def __init__(self):
            super().__init__(None)
            self._service = service
            self.receive_handlers['_rpc_applicationSentData:'] = self.page_data
            self.on_connection_lost = disconnected

        async def connect(self):
            if self._recv_task is not None:
                return
            await self._report_identifier()
            await self._handle_recv(await self.service.recv_plist())
            self._recv_task = asyncio.create_task(self.receive_loop())

        async def receive_loop(self):
            try:
                while True:
                    await self._handle_recv(await self.service.recv_plist())
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._connection_lost = True
                disconnected(error)

        async def page_data(self, argument):
            receive(argument.get('WIRDestinationKey'), json.loads(argument['WIRMessageDataKey']))

    return DirectInspector()


@dataclass
class NetworkRequest:
    url: str
    method: str
    headers: dict = field(repr=False)
    request_id: str
    target_id: str
    resource_type: str = ''
    post_data: str | None = field(default=None, repr=False)


@dataclass
class NetworkResponse:
    url: str
    status: int
    headers: dict = field(repr=False)
    mime_type: str
    request_id: str
    target_id: str
    _session: object = field(repr=False)
    _finished: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _error: str | None = field(default=None, repr=False)
    _metadata_bytes: int = field(default=0, repr=False)

    async def body(self, *, timeout=30, max_bytes=16 * 1024**2):
        """Read the native response after loadingFinished, including cached bodies.

        A body can be unavailable after eviction, redirect, or process destruction;
        that is an error, never an empty successful response or another request.
        """
        if type(max_bytes) is not int or not 1 <= max_bytes <= 16 * 1024**2:
            raise ValueError('max_bytes must be between 1 and 16 MiB')
        async with asyncio.timeout(_timeout(timeout)):
            await self._finished.wait()
            if self._error:
                raise InspectorError(self._error)
            result = await self._session._command('Network.getResponseBody',
                target=self.target_id, requestId=self.request_id)
            body = (base64.b64decode(result['body'], validate=True) if result['base64Encoded']
                    else result['body'].encode('utf-8'))
            if len(body) > max_bytes:
                raise InspectorError('Response body exceeds the requested byte limit')
            return body

    async def text(self, *, encoding='utf-8', **options):
        return (await self.body(**options)).decode(encoding)

    async def json(self, **options):
        return json.loads(await self.body(**options))


class _Expectation:
    def __init__(self, session, kind, predicate, timeout):
        self.session, self.kind, self.predicate = session, kind, predicate
        self.timeout = _timeout(timeout)
        self.future = None
        self.value = None

    async def __aenter__(self):
        self.session._check()
        self.future = asyncio.get_running_loop().create_future()
        self.session._waiters.add(self)
        self.deadline = asyncio.get_running_loop().time() + self.timeout
        return self

    async def __aexit__(self, kind, error, traceback):
        try:
            if kind is None:
                async with asyncio.timeout_at(self.deadline):
                    self.value = await self.future
        finally:
            self.session._waiters.discard(self)
            if not self.future.done():
                self.future.cancel()
            elif not self.future.cancelled():
                self.future.exception()  # Consume failure when the user's action also failed.


class InspectorSession:
    """Own native inspection of one existing Safari tab.

    A session releases its subscriptions and connection on exit. It never closes
    an existing tab, clears site state, or retries an action. The optional native
    Browser provides navigation/input/screenshot methods; inspection reads use
    WebKit's DOM and Network domains, with no Runtime.evaluate calls.
    """
    def __init__(self, *, transport=None, browser=None, url=None, timeout=30,
                 max_requests=512, max_events=128, max_metadata_bytes=4 * 1024**2):
        self.timeout = _timeout(timeout)
        for limit in (max_requests, max_events):
            if type(limit) is not int or not 1 <= limit <= 4096:
                raise ValueError('Retention limits must be integers between 1 and 4096')
        self.transport = transport or InspectorTransport(timeout=timeout)
        self.browser, self.url = browser, url
        self.max_requests = max_requests
        if type(max_metadata_bytes) is not int or not 1024 <= max_metadata_bytes <= 64 * 1024**2:
            raise ValueError('max_metadata_bytes must be between 1 KiB and 64 MiB')
        self.max_metadata_bytes = max_metadata_bytes
        self.retained_metadata_bytes = 0
        self.events = deque(maxlen=max_events)
        self.dropped_events = self.evicted_requests = 0
        self.responses = OrderedDict()
        self._pending, self._outer = {}, {}
        self._root_pending = {}
        self._waiters, self._tasks, self._targets = set(), set(), set()
        self._target_ready = asyncio.Event()
        self._id = 0
        self._session_id = str(uuid.uuid4()).upper()
        self._inspector = self._app = self._page = self.target_id = None
        self._send_lock = asyncio.Lock()
        self._native_lock = asyncio.Lock()
        self._native_tasks = set()
        self.cleanup_errors = []
        self._closed = False
        self._failure = None
        self.document_generation = 0

    def _check(self):
        if self._closed or self._failure:
            raise InspectorError(self._failure or 'Inspector session is closed')

    def _fail(self, message):
        self._failure = message
        for _, future in self._pending.values():
            if not future.done():
                future.set_exception(InspectorError(message))
        for future in self._root_pending.values():
            if not future.done():
                future.set_exception(InspectorError(message))
        for waiter in self._waiters:
            if not waiter.future.done():
                waiter.future.set_exception(InspectorError(message))
        for response in self.responses.values():
            response._error = message
            response._finished.set()
        self._target_ready.set()

    async def __aenter__(self):
        if self._inspector is not None or self._closed:
            raise InspectorError('Inspector session is single-use')
        try:
            async with asyncio.timeout(self.timeout):
                service = await self.transport.open()
                service.max_frame_size = 32 * 1024**2
                self._inspector = _direct_inspector(service, self._receive,
                    lambda error=None: self._fail('Native Inspector connection closed' +
                        (f': {type(error).__name__}: {error}' if error is not None else '')))
                await self._inspector.connect()
                await self._inspector._get_connected_applications()
                # Listings are asynchronous. Wait for an unambiguous matching tab;
                # never silently choose another tab on an explicitly requested URL.
                while True:
                    matches = [(app, page) for app in self._inspector.connected_application.values()
                               if app.bundle == 'com.apple.mobilesafari'
                               for page in self._inspector.application_pages.get(app.id_, {}).values()
                               if page.type_.value in ('WIRTypeWebPage', 'WIRTypeWeb')
                               and (self.url is None or page.web_url == self.url)]
                    if len(matches) > 1:
                        raise InspectorError('More than one Safari tab matches; specify a unique URL')
                    if matches:
                        self._app, self._page = matches[0]
                        if self._page.web_connection_id:
                            raise InspectorError('Safari tab is already held by an Inspector connection')
                        break
                    self._check()
                    await asyncio.sleep(0.05)
                await self._inspector.setup_inspector_socket(self._session_id,
                    self._app.id_, self._page.id_, automatically_pause=False)
                await self._target_ready.wait()
                self._check()
                await self._root_command('Target.setPauseOnStart', pauseOnStart=True)
                return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *args):
        await self.close()

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        def finished(task):
            self._tasks.discard(task)
            if not task.cancelled() and task.exception():
                self._fail('Native page initialization failed: ' + str(task.exception()))
        task.add_done_callback(finished)

    async def _setup_target(self, target, paused):
        try:
            await self._command('Network.enable', target=target)
            await self._command('Page.enable', target=target)
        finally:
            if paused:
                # Resume even if setup fails or is cancelled. The closing session
                # also tears down the native inspection socket; no debugger is enabled.
                async with asyncio.timeout(5):
                    await self._send_outer('Target.resume', targetId=target)
        if self.target_id == target:
            self._target_ready.set()

    def _receive(self, destination, message):
        if destination != self._session_id or self._closed:
            return
        if 'id' in message:
            root = self._root_pending.get(message['id'])
            if root is not None and not root.done():
                if 'error' in message:
                    root.set_exception(InspectorError(str(message['error'])))
                else:
                    root.set_result(message.get('result', {}))
                return
            inner = self._outer.pop(message['id'], None)
            if 'error' in message and inner in self._pending:
                _, future = self._pending[inner]
                if not future.done():
                    future.set_exception(InspectorError(str(message['error'])))
            return
        method, params = message.get('method'), message.get('params', {})
        if method == 'Target.targetCreated':
            info = params['targetInfo']
            if info.get('type', 'page') != 'page':
                if info.get('isPaused', False):
                    self._spawn(self._send_outer('Target.resume', targetId=info['targetId']))
                return
            target = info['targetId']
            self._targets.add(target)
            if not info.get('isProvisional', False):
                self.target_id = target
            self._spawn(self._setup_target(target, info.get('isPaused', False)))
        elif method == 'Target.didCommitProvisionalTarget':
            self.target_id = params['newTargetId']
            self.document_generation += 1
        elif method == 'Target.targetDestroyed':
            target = params['targetId']
            self._targets.discard(target)
            for pending_target, future in self._pending.values():
                if pending_target == target and not future.done():
                    future.set_exception(DocumentChangedError('Page process was destroyed during the command'))
            for response in self.responses.values():
                if response.target_id == target:
                    response._error = 'Response page process was destroyed'
                    response._finished.set()
        elif method == 'Target.dispatchMessageFromTarget':
            target = params['targetId']
            content = json.loads(params['message'])
            if 'id' in content:
                pending = self._pending.get(content['id'])
                if pending and pending[0] == target and not pending[1].done():
                    if 'error' in content:
                        pending[1].set_exception(InspectorError(str(content['error'])))
                    else:
                        pending[1].set_result(content['result'])
            else:
                self._event(target, content)

    def _event(self, target, event):
        method, p = event.get('method'), event.get('params', {})
        if len(self.events) == self.events.maxlen:
            self.dropped_events += 1
        # Diagnostic event names only; bodies and credentials aren't logged.
        self.events.append({'method': method, 'target_id': target, 'monotonic': time.monotonic()})
        if method == 'DOM.documentUpdated' and target == self.target_id:
            self.document_generation += 1
        key = (target, p.get('requestId'))
        if method == 'Network.requestWillBeSent':
            request = p['request']
            value = NetworkRequest(request['url'], request['method'], request.get('headers', {}),
                p['requestId'], target, p.get('type', ''), request.get('postData'))
            # Redirects reuse request IDs; an earlier response cannot return the
            # subsequent request's body under the same ID.
            previous = self.responses.pop(key, None)
            if previous:
                self.retained_metadata_bytes -= previous._metadata_bytes
                previous._error = 'Response was replaced by a redirect or reused request ID'
                previous._finished.set()
            self._publish('request', value)
        elif method == 'Network.responseReceived':
            response = p['response']
            value = NetworkResponse(response['url'], response['status'], response.get('headers', {}),
                response.get('mimeType', ''), p['requestId'], target, self)
            value._metadata_bytes = len(json.dumps(response, ensure_ascii=False).encode('utf-8'))
            previous = self.responses.pop(key, None)
            if previous:
                self.retained_metadata_bytes -= previous._metadata_bytes
                previous._error = 'Response metadata was replaced for this request ID'
                previous._finished.set()
            self.responses[key] = value
            self.retained_metadata_bytes += value._metadata_bytes
            while (len(self.responses) > self.max_requests
                   or self.retained_metadata_bytes > self.max_metadata_bytes):
                _, old = self.responses.popitem(last=False)
                self.retained_metadata_bytes -= old._metadata_bytes
                old._error = 'Response was evicted from bounded session retention'
                old._finished.set()
                self.evicted_requests += 1
            self._publish('response', value)
        elif method in ('Network.loadingFinished', 'Network.loadingFailed'):
            response = self.responses.get(key)
            if response:
                if method.endswith('Failed'):
                    response._error = p.get('errorText', 'Network request failed')
                response._finished.set()

    def _publish(self, kind, value):
        for waiter in tuple(self._waiters):
            if waiter.kind != kind or waiter.future.done():
                continue
            try:
                if waiter.predicate(value):
                    waiter.future.set_result(value)
            except Exception as error:
                waiter.future.set_exception(error)

    async def _send_outer(self, method, **params):
        self._id += 1
        outer = self._id
        await self._inspector.send_socket_data(self._session_id, self._app.id_, self._page.id_,
                                               {'id': outer, 'method': method, 'params': params})
        return outer

    async def _root_command(self, method, **params):
        self._check()
        future = asyncio.get_running_loop().create_future()
        outer = None
        try:
            async with asyncio.timeout(self.timeout):
                async with self._send_lock:
                    outer = self._id + 1
                    self._root_pending[outer] = future
                    await self._send_outer(method, **params)
                return await future
        finally:
            self._root_pending.pop(outer, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def _command(self, method, *, target=None, **params):
        self._check()
        if target is None:
            async with asyncio.timeout(self.timeout):
                while self.target_id not in self._targets:
                    self._check()
                    await asyncio.sleep(0.05)
            target = self.target_id
        if target not in self._targets:
            raise InspectorError('The requested page process is no longer available')
        self._id += 1
        inner = self._id
        future = asyncio.get_running_loop().create_future()
        self._pending[inner] = (target, future)
        outer = None
        try:
            async with asyncio.timeout(self.timeout):
                async with self._send_lock:
                    # Record before awaiting send, since a fast native reply can arrive immediately.
                    outer = self._id + 1
                    self._outer[outer] = inner
                    await self._send_outer('Target.sendMessageToTarget', targetId=target,
                        message=json.dumps({'id': inner, 'method': method, 'params': params}))
                return await future
        finally:
            self._pending.pop(inner, None)
            if outer is not None:
                self._outer.pop(outer, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def content(self):
        """Serialize the current rendered DOM; this is not the original HTML response."""
        target, generation, document = await self._document()
        result = await self._command('DOM.getOuterHTML', target=target, nodeId=document['root']['nodeId'])
        self._same_document(target, generation)
        return result['outerHTML']

    def _same_document(self, target, generation):
        if target != self.target_id or generation != self.document_generation:
            raise DocumentChangedError('Document changed during the read')

    async def _document(self):
        async with asyncio.timeout(self.timeout):
            while self.target_id not in self._targets:
                self._check()
                await asyncio.sleep(0.05)
        target, generation = self.target_id, self.document_generation
        document = await self._command('DOM.getDocument', target=target)
        self._same_document(target, generation)
        return target, generation, document

    async def query_selector(self, selector, *, strategy='css'):
        if not isinstance(selector, str) or not 1 <= len(selector) <= 8192:
            raise ValueError('selector must contain 1–8192 characters')
        if strategy not in ('css', 'xpath'):
            raise ValueError('strategy must be css or xpath')
        target, generation, document = await self._document()
        if strategy == 'css':
            result = await self._command('DOM.querySelector', target=target, nodeId=document['root']['nodeId'], selector=selector)
            if target != self.target_id or generation != self.document_generation:
                raise DocumentChangedError('Document changed during selector lookup')
            return result.get('nodeId') or None
        search = await self._command('DOM.performSearch', target=target, query=selector, nodeIds=[document['root']['nodeId']])
        try:
            if not search['resultCount']:
                return None
            result = await self._command('DOM.getSearchResults', target=target, searchId=search['searchId'], fromIndex=0, toIndex=1)
            if target != self.target_id or generation != self.document_generation:
                raise DocumentChangedError('Document changed during XPath lookup')
            return result['nodeIds'][0]
        finally:
            if target in self._targets:
                await self._command('DOM.discardSearchResults', target=target, searchId=search['searchId'])

    async def wait_for_selector(self, selector, *, strategy='css', state='attached', timeout=30, interval=0.25):
        if state not in ('attached', 'detached'):
            raise ValueError('state must be attached or detached; visibility is not inferred')
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 0.05 <= interval <= 5:
            raise ValueError('interval must be between 0.05 and 5 seconds')
        async with asyncio.timeout(_timeout(timeout)):
            while True:
                try:
                    node = await self.query_selector(selector, strategy=strategy)
                except DocumentChangedError:
                    await asyncio.sleep(interval)
                    continue
                if (node is not None) == (state == 'attached'):
                    return node
                await asyncio.sleep(interval)

    async def wait_for_url(self, pattern, *, timeout=30):
        """Wait for a committed document URL matching a glob; redirects stay explicit."""
        if not isinstance(pattern, str) or not pattern:
            raise ValueError('Expected a nonempty URL glob')
        async with asyncio.timeout(_timeout(timeout)):
            while True:
                try:
                    _, _, document = await self._document()
                    url = document['root'].get('documentURL', '')
                    if fnmatch.fnmatchcase(url, pattern):
                        return url
                except DocumentChangedError:
                    pass
                await asyncio.sleep(0.25)

    async def wait_for_timeout(self, milliseconds):
        if isinstance(milliseconds, bool) or not isinstance(milliseconds, (int, float)) or not 0 <= milliseconds <= 300000:
            raise ValueError('milliseconds must be between 0 and 300000')
        self._check()
        await asyncio.sleep(milliseconds / 1000)
        self._check()

    def _expect(self, kind, predicate, timeout):
        if isinstance(predicate, str):
            pattern = predicate
            predicate = lambda value: fnmatch.fnmatchcase(value.url, pattern)
        if not callable(predicate):
            raise ValueError('Expected a URL glob or synchronous predicate')
        return _Expectation(self, kind, predicate, timeout)

    def expect_request(self, predicate, *, timeout=30):
        return self._expect('request', predicate, timeout)

    def expect_response(self, predicate, *, timeout=30):
        return self._expect('response', predicate, timeout)

    async def reload(self):
        return await self._command('Page.reload')

    async def goto(self, url):
        if self.browser is None:
            raise InspectorError('Native navigation requires a Browser instance')
        def navigate():
            with self.browser._lock:
                foreground = self.browser.guest.call('apps.foreground')
                if (foreground.get('verified') is not True or foreground.get('pid') != self._app.pid
                        or foreground.get('bundle_id') != 'com.apple.mobilesafari'):
                    raise InspectorError('The inspected Safari application is not the verified foreground app')
                result = self.browser.goto(url, reuse_tab=True)
                if result.get('method') != 'native_address_bar':
                    raise InspectorError('Native navigation opened a different tab; inspected tab readiness is unknown')
                return result
        return await self._native(navigate)

    async def _native(self, operation, *args, **kwargs):
        async with self._native_lock:
            self._check()
            task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
            self._native_tasks.add(task)
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # A native input already sent cannot be recalled. Finish that
                # bounded exchange before cleanup; never let a worker outlive
                # the session or silently replay its action.
                with contextlib.suppress(Exception):
                    await asyncio.shield(task)
                raise
            finally:
                if task.done():
                    self._native_tasks.discard(task)
                else:
                    # A second cancellation can interrupt the cancellation wait.
                    # Keep ownership until the worker finishes or close joins it.
                    task.add_done_callback(self._native_tasks.discard)

    async def screenshot(self, path=None, *, overwrite=False):
        if self.browser is None:
            raise InspectorError('Native capture requires a Browser instance')
        return await self._native(self.browser.screenshot, path, overwrite=overwrite)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        self._fail('Inspector session closed')
        await asyncio.gather(*self._native_tasks, return_exceptions=True)
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._inspector is not None:
            async def cleanup(operation):
                try:
                    await asyncio.wait_for(operation, 5)
                except Exception as error:
                    self.cleanup_errors.append(f'{type(error).__name__}: {error}')
            if self._app is not None and self._page is not None:
                await cleanup(self._send_outer('Target.setPauseOnStart', pauseOnStart=False))
                await cleanup(self._inspector.teardown_inspector_socket(
                    self._session_id, self._app.id_, self._page.id_))
            await cleanup(self._inspector.close())
        await self.transport.close()
        self.responses.clear()
        self.retained_metadata_bytes = 0
