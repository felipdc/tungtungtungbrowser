**Shopee recovery, 18–19 November 2026**

The final validation recovered signed-out Shopee access on a 32 GiB host: 6/6 products returned matching network JSON and displayed unobstructed native product views. Each attempt used a new Evomi Brazilian locked session; all 6 exits were distinct and verified inside Safari. Product API responses arrived in 30.2–37.8 seconds, with a 31.2-second median. These timings start at product navigation and exclude proxy preparation and manual review.

| Product | Item ID | Product API |
| --- | --- | --- |
| Esmalte Top Coat Brilho (Que Tudo Termine Em Brilho) - Dailus | 20516902280 | 37.8 s |
| Conjunto De Talher 42 peças Aço Inox 14 Facas14 Garfos e 14 Colheres Talheres De Qualidade Resistente Para Cozinha Mesa | 22793246974 | 30.2 s |
| Short Bermuda bermudinha Básico Cós Alto varias cores zero transparência com forro KIT 2 UNIDADES | 20797463934 | 30.7 s |
| Pente Fino com Cabo Metálico Para Cabelo e Penteado - Envio Imediato - Unidade | 19697411039 | 30.4 s |
| Hidratante Corporal (Creme Base Universal Super Hidratante) – 1 Kg | 19797678415 | 31.6 s |
| BOTA FEMININA CANO CURTO DAKOTA  LANÇAMENTO OFERTA!! | 22034892887 | 31.7 s |

This validates one sequential six-product run, not long-run reliability across arbitrary proxy exits. The separate initialization attempt returned product data but retained consent/language UI and later failed during neutral-page departure; its five remaining planned products were not attempted. Those outcomes remain in the evidence.

The current SDK snapshot, `7ccd194`, remained in use with the pre-existing local phone-isolation changes. No causal bad SDK commit was established. The earlier working `4d41b41` run reset Safari-owned storage and reused a retained tab; the failed later workflow inherited storage and accumulated tabs. `Browser.goto` already defaulted to opening a tab in the older commit. These workflow differences prevent attributing the regression to the commit alone.

A native display mismatch was also corrected: the original guest exposed 393×852 pixels at scale 1 for its iPhone17,3 identity. Restoring the original LLB scale instruction, framebuffer and MobileGestalt display cache yielded 1179×2556 pixels, 393×852 points, and scale 3. Display correction alone had not restored access in the preceding diagnostic. The evidence establishes a working combined procedure; Shopee's private rejection signal remains unknown.

The procedure used on the disposable guest was:

1. Restore the native display configuration and verify the point size and scale through the guest API and a browser probe.
2. In Safari settings, clear all history and website data with Close All Tabs enabled. This resets browser state and is not required before every subsequent product.
3. Open one Safari tab and complete Portuguese visitor setup. The essential-only preference flow did not persistently dismiss the banner in this run. A native **Aceitar todos os cookies** click dismissed it; the next five products needed no further visitor setup. The product API had already succeeded before that click.
4. Before each product, create a new Evomi BR locked session and verify that Safari's public IP matches the relay's BR exit. Keep this session stable for the page's dependent requests and native UI actions. Leave Shopee for a neutral page before replacing the relay.
5. Validate the expected shop/item IDs, title, price, currency and signed-out state in the actual network response. Review the native screenshot separately. HTTP 200 is insufficient: earlier rejected responses contained application error `90309999`.

The host was a `c8a.4xlarge` with 32 GiB RAM. The VM container limit was raised from 9 to 16 GiB; the guest retained 3 GiB and three virtual CPUs. Later samples used about 9.5–9.6 GiB in the container, with no host-side OOM kill. These observations do not establish memory pressure as the rejection cause.

Local evidence is in the ignored `artifacts/shopee-recovery-045/` directory. `final-validation.json` checks the six reviewed screenshots against the hash-verified final archive, network response identity/source, signed-out state, and both entry/departure route proofs. `final-analysis.json` retains the initial incomplete attempt separately. Screenshots are under `downloads/final/trials/baseline/1/post-init/`; the first product's unobstructed view is `case-01/ui/accept-cookies/after.png`, and products 2–6 use `case-NN/final.png`.

The guest was shut down cleanly and preserved privately with an archive hash receipt in `preserved-native.json`. `initialized-payload.json` identifies the matching SDK, helpers and harness package. The guest archive depends on the original source volume and recovery AMI named in its manifest; it is not a standalone disk image. `stage-initialized-recovery.py` prepares a fresh-host restore script that verifies hashes and refuses to overwrite an existing guest. A second cold restore of this final snapshot was not performed.

`cleanup-verified.json` confirms that the temporary host was terminated and its root volume deleted, while the original source volume, AMI and recovery objects were preserved. The recovered guest is saved rather than left running. This record documents guest configuration and the capture workflow; it does not change default SDK behavior.
