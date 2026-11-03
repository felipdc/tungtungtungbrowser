/* Verify lavapipe backing reclamation using real Vulkan allocations and work. */
#define _GNU_SOURCE
#include <vulkan/vulkan.h>
#include <dirent.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MIB (1024ull * 1024)
#define CHECK(expr) do { if (!(expr)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #expr); exit(2); } } while (0)
#define VK(expr) do { VkResult result_ = (expr); if (result_ != VK_SUCCESS) { fprintf(stderr, "FAIL line %d: %s returned %d\n", __LINE__, #expr, result_); exit(2); } } while (0)
static VkDevice device;
static uint32_t memory_type;
static atomic_int thread_errors;
static VkDeviceMemory alloc(VkDeviceSize size, const void *next) {
    VkMemoryAllocateInfo info = {.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .pNext=next, .allocationSize=size, .memoryTypeIndex=memory_type};
    VkDeviceMemory memory;
    VK(vkAllocateMemory(device, &info, NULL, &memory));
    return memory;
}
static uint8_t *map(VkDeviceMemory memory, VkDeviceSize size) {
    void *p=NULL; VK(vkMapMemory(device, memory, 0, size, 0, &p)); return p;
}
static void pattern(uint8_t *p, size_t size, uint8_t value) { memset(p, value, size); }
static int valid(const uint8_t *p, size_t size, uint8_t value) {
    for (size_t n=0;n<size;n+=4096) if (p[n]!=value || p[n+4095]!=value) return 0;
    return 1;
}
static int allocation_fd(void) {
    DIR *dir=opendir("/proc/self/fd"); CHECK(dir); struct dirent *entry; int found=-1;
    while ((entry=readdir(dir))) {
        char path[512],target[256]; snprintf(path,sizeof(path),"/proc/self/fd/%s",entry->d_name);
        ssize_t n=readlink(path,target,sizeof(target)-1); if (n<0) continue; target[n]=0;
        if (strstr(target,"memfd:allocation fd")) { CHECK(found==-1); found=atoi(entry->d_name); }
    }
    closedir(dir); CHECK(found>=0); return found;
}
static uint64_t resident_file_bytes(int fd) { struct stat st; CHECK(fstat(fd,&st)==0); return (uint64_t)st.st_blocks*512; }
static void *churn(void *arg) {
    uint8_t value=(uint8_t)(uintptr_t)arg; const size_t size=8*MIB;
    for (unsigned n=0;n<40;n++) {
        VkDeviceMemory m=alloc(size,NULL); uint8_t *p=map(m,size); pattern(p,size,value);
        if (!valid(p,size,value)) atomic_fetch_add(&thread_errors,1);
        vkUnmapMemory(device,m); vkFreeMemory(device,m,NULL);
    }
    return NULL;
}
int main(int argc,char **argv) {
    CHECK(argc==2 && (!strcmp(argv[1],"retained") || !strcmp(argv[1],"released")));
    int expect_released=!strcmp(argv[1],"released");
    VkApplicationInfo app={.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,.pApplicationName="allocation-reclaim-check",.apiVersion=VK_API_VERSION_1_1};
    VkInstanceCreateInfo instance_info={.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,.pApplicationInfo=&app};
    VkInstance instance; VK(vkCreateInstance(&instance_info,NULL,&instance));
    uint32_t count=0; VK(vkEnumeratePhysicalDevices(instance,&count,NULL)); CHECK(count==1);
    VkPhysicalDevice physical; VK(vkEnumeratePhysicalDevices(instance,&count,&physical));
    VkPhysicalDeviceProperties props; vkGetPhysicalDeviceProperties(physical,&props);
    CHECK(props.deviceType==VK_PHYSICAL_DEVICE_TYPE_CPU && strstr(props.deviceName,"llvmpipe"));
    VkPhysicalDeviceMemoryProperties memory_props; vkGetPhysicalDeviceMemoryProperties(physical,&memory_props);
    memory_type=UINT32_MAX;
    for (uint32_t n=0;n<memory_props.memoryTypeCount;n++) if ((memory_props.memoryTypes[n].propertyFlags & (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT))==(VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) { memory_type=n; break; }
    CHECK(memory_type!=UINT32_MAX);
    uint32_t queue_count=0; vkGetPhysicalDeviceQueueFamilyProperties(physical,&queue_count,NULL);
    VkQueueFamilyProperties *queues=calloc(queue_count,sizeof(*queues)); CHECK(queues);
    vkGetPhysicalDeviceQueueFamilyProperties(physical,&queue_count,queues); uint32_t queue=UINT32_MAX;
    for (uint32_t n=0;n<queue_count;n++) if (queues[n].queueFlags&VK_QUEUE_GRAPHICS_BIT) { queue=n; break; }
    free(queues); CHECK(queue!=UINT32_MAX);
    float priority=1; VkDeviceQueueCreateInfo q={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,.queueFamilyIndex=queue,.queueCount=1,.pQueuePriorities=&priority};
    const char *extensions[]={VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME,VK_EXT_EXTERNAL_MEMORY_HOST_EXTENSION_NAME};
    VkDeviceCreateInfo d={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,.queueCreateInfoCount=1,.pQueueCreateInfos=&q,.enabledExtensionCount=2,.ppEnabledExtensionNames=extensions};
    VK(vkCreateDevice(physical,&d,NULL,&device));
    const size_t size=64*MIB; VkDeviceMemory left=alloc(size,NULL),middle=alloc(size,NULL),right=alloc(size,NULL);
    uint8_t *lp=map(left,size),*mp=map(middle,size),*rp=map(right,size);
    pattern(lp,size,0x31);pattern(mp,size,0);pattern(rp,size,0x73);
    /* Complete a real device transfer before freeing its backing allocation. */
    VkBufferCreateInfo bi={.sType=VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,.size=size,.usage=VK_BUFFER_USAGE_TRANSFER_DST_BIT,.sharingMode=VK_SHARING_MODE_EXCLUSIVE};
    VkBuffer buffer; VK(vkCreateBuffer(device,&bi,NULL,&buffer));
    VkMemoryRequirements requirements;vkGetBufferMemoryRequirements(device,buffer,&requirements);
    CHECK(requirements.size<=size && (requirements.memoryTypeBits&(1u<<memory_type)));
    VK(vkBindBufferMemory(device,buffer,middle,0));
    VkCommandPoolCreateInfo pci={.sType=VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,.queueFamilyIndex=queue};
    VkCommandPool pool;VK(vkCreateCommandPool(device,&pci,NULL,&pool));
    VkCommandBufferAllocateInfo cai={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,.commandPool=pool,.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY,.commandBufferCount=1};
    VkCommandBuffer command;VK(vkAllocateCommandBuffers(device,&cai,&command));
    VkCommandBufferBeginInfo begin={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    VK(vkBeginCommandBuffer(command,&begin));vkCmdFillBuffer(command,buffer,0,size,0x52525252);
    VkMemoryBarrier barrier={.sType=VK_STRUCTURE_TYPE_MEMORY_BARRIER,.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT,.dstAccessMask=VK_ACCESS_HOST_READ_BIT};
    vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&barrier,0,NULL,0,NULL);VK(vkEndCommandBuffer(command));
    VkQueue work_queue;vkGetDeviceQueue(device,queue,0,&work_queue);
    VkSubmitInfo submit={.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,.pCommandBuffers=&command};
    VK(vkQueueSubmit(work_queue,1,&submit,VK_NULL_HANDLE));VK(vkQueueWaitIdle(work_queue));
    CHECK(valid(mp,size,0x52));vkDestroyBuffer(device,buffer,NULL);vkDestroyCommandPool(device,pool,NULL);
    int fd=allocation_fd(); uint64_t before=resident_file_bytes(fd);
    vkUnmapMemory(device,middle); vkFreeMemory(device,middle,NULL);
    uint64_t after=resident_file_bytes(fd);
    CHECK(valid(lp,size,0x31) && valid(rp,size,0x73));
    if (expect_released) CHECK(before>=after+size); else CHECK(before==after);
    VkDeviceMemory reused=alloc(size,NULL); uint8_t *reuse=map(reused,size);pattern(reuse,size,0x94);
    CHECK(valid(reuse,size,0x94) && valid(lp,size,0x31) && valid(rp,size,0x73));
    vkUnmapMemory(device,reused);vkFreeMemory(device,reused,NULL);
    pthread_t workers[4]; for (uintptr_t n=0;n<4;n++) CHECK(!pthread_create(&workers[n],NULL,churn,(void *)(n+1)));
    for (unsigned n=0;n<4;n++) CHECK(!pthread_join(workers[n],NULL));
    CHECK(atomic_load(&thread_errors)==0 && valid(lp,size,0x31) && valid(rp,size,0x73));
    /* An exported allocation remains valid through its separately imported owner. */
    VkExportMemoryAllocateInfo ex={.sType=VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO,.handleTypes=VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT};
    VkDeviceMemory exported=alloc(16*MIB,&ex); uint8_t *ep=map(exported,16*MIB);pattern(ep,16*MIB,0xb5);
    PFN_vkGetMemoryFdKHR getfd=(PFN_vkGetMemoryFdKHR)vkGetDeviceProcAddr(device,"vkGetMemoryFdKHR");CHECK(getfd);
    VkMemoryGetFdInfoKHR fi={.sType=VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR,.memory=exported,.handleType=VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT};int external_fd;VK(getfd(device,&fi,&external_fd));
    VkImportMemoryFdInfoKHR im={.sType=VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR,.handleType=VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT,.fd=external_fd};
    VkDeviceMemory imported=alloc(16*MIB,&im); uint8_t *ip=map(imported,16*MIB);CHECK(valid(ip,16*MIB,0xb5));
    vkUnmapMemory(device,exported);vkFreeMemory(device,exported,NULL);CHECK(valid(ip,16*MIB,0xb5));
    vkUnmapMemory(device,imported);vkFreeMemory(device,imported,NULL);
    /* Freeing a Vulkan host-pointer import must not discard its caller's storage. */
    VkPhysicalDeviceExternalMemoryHostPropertiesEXT hp={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT};
    VkPhysicalDeviceProperties2 p2={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,.pNext=&hp};vkGetPhysicalDeviceProperties2(physical,&p2);
    size_t alignment=hp.minImportedHostPointerAlignment;CHECK(alignment && alignment<=16*MIB);
    void *host=NULL;CHECK(!posix_memalign(&host,alignment,16*MIB));pattern(host,16*MIB,0xd6);
    PFN_vkGetMemoryHostPointerPropertiesEXT gethost=(PFN_vkGetMemoryHostPointerPropertiesEXT)vkGetDeviceProcAddr(device,"vkGetMemoryHostPointerPropertiesEXT");CHECK(gethost);
    VkMemoryHostPointerPropertiesEXT host_props={.sType=VK_STRUCTURE_TYPE_MEMORY_HOST_POINTER_PROPERTIES_EXT};
    VK(gethost(device,VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT,host,&host_props));CHECK(host_props.memoryTypeBits&(1u<<memory_type));
    VkImportMemoryHostPointerInfoEXT hi={.sType=VK_STRUCTURE_TYPE_IMPORT_MEMORY_HOST_POINTER_INFO_EXT,.handleType=VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT,.pHostPointer=host};
    VkDeviceMemory host_import=alloc(16*MIB,&hi);vkFreeMemory(device,host_import,NULL);CHECK(valid(host,16*MIB,0xd6));free(host);
    vkUnmapMemory(device,left);vkFreeMemory(device,left,NULL);vkUnmapMemory(device,right);vkFreeMemory(device,right,NULL);
    printf("{\"device\":\"%s\",\"mode\":\"%s\",\"backing_before_bytes\":%llu,\"backing_after_bytes\":%llu,\"released_bytes\":%llu,\"device_fill_verified\":true,\"neighbor_integrity\":true,\"concurrent_reuse_iterations\":160,\"external_fd_lifetime\":true,\"host_import_preserved\":true}\n",props.deviceName,argv[1],(unsigned long long)before,(unsigned long long)after,(unsigned long long)(before-after));
    vkDestroyDevice(device,NULL);vkDestroyInstance(instance,NULL);return 0;
}
