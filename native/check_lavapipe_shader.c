/* CPU-only Vulkan shader correctness and first/repeated-dispatch measurements.
 * Compile the adjacent .comp file with glslangValidator before running.
 * These timings describe this compute workload, not native browser startup.
 */
#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>

#define CHECK(expr) do { if (!(expr)) { \
    fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #expr); exit(2); \
} } while (0)
#define VK(expr) do { VkResult result_ = (expr); if (result_ != VK_SUCCESS) { \
    fprintf(stderr, "FAIL line %d: %s returned %d\n", __LINE__, #expr, result_); exit(2); \
} } while (0)
enum { LANES = 64, GROUPS = 16, COUNT = LANES * GROUPS, VARIANTS = 8, REPEATS = 16 };

static double monotonic(void) {
    struct timespec t;
    CHECK(clock_gettime(CLOCK_MONOTONIC, &t) == 0);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static uint32_t expected_hash(uint32_t id, uint32_t rounds) {
    uint32_t value = id ^ UINT32_C(0xa5a5a5a5);
    for (uint32_t n = 0; n < rounds; ++n) {
        value = value * UINT32_C(1664525) + UINT32_C(1013904223);
        value ^= value >> 13;
    }
    return value;
}

static void verify(const uint32_t *data, uint32_t rounds) {
    for (uint32_t id = 0; id < COUNT; ++id) {
        uint32_t neighbor = (id / LANES) * LANES + (id + 17) % LANES;
        float fraction = (float)(id & 1023) * 0.25f + (float)rounds * 0.125f;
        fraction = fraction * 0.5f + (float)((id + 3) & 127) * 0.25f;
        uint32_t bits;
        memcpy(&bits, &fraction, sizeof(bits));
        uint32_t expected[] = {expected_hash(id, rounds), expected_hash(neighbor, rounds),
                               bits, rounds ^ id};
        for (unsigned component = 0; component < 4; ++component) {
            if (data[id * 4 + component] != expected[component]) {
                fprintf(stderr, "Mismatch rounds=%u id=%u component=%u got=%08x expected=%08x\n",
                        rounds, id, component, data[id * 4 + component], expected[component]);
                exit(3);
            }
        }
    }
}

int main(int argc, char **argv) {
    CHECK(argc == 2);
    const char *perf = getenv("GALLIVM_PERF");
    CHECK(!perf || !strcmp(perf, "") || !strcmp(perf, "nopt"));
    FILE *source = fopen(argv[1], "rb");
    CHECK(source && fseek(source, 0, SEEK_END) == 0);
    long bytes = ftell(source);
    CHECK(bytes > 0 && bytes <= 1024 * 1024 && bytes % 4 == 0);
    rewind(source);
    uint32_t *spirv = malloc((size_t)bytes);
    CHECK(spirv && fread(spirv, 1, (size_t)bytes, source) == (size_t)bytes);
    CHECK(fclose(source) == 0 && spirv[0] == UINT32_C(0x07230203));

    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "cpu-shader-check", .apiVersion = VK_API_VERSION_1_1};
    VkInstanceCreateInfo ii = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app};
    VkInstance instance;
    VK(vkCreateInstance(&ii, NULL, &instance));
    uint32_t count = 0;
    VK(vkEnumeratePhysicalDevices(instance, &count, NULL));
    CHECK(count == 1);
    VkPhysicalDevice physical;
    VK(vkEnumeratePhysicalDevices(instance, &count, &physical));
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(physical, &props);
    CHECK(props.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU && strstr(props.deviceName, "llvmpipe"));
    uint32_t queue_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, NULL);
    VkQueueFamilyProperties *families = calloc(queue_count, sizeof(*families));
    CHECK(families);
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, families);
    uint32_t family = UINT32_MAX;
    for (uint32_t n = 0; n < queue_count; ++n)
        if (families[n].queueFlags & VK_QUEUE_COMPUTE_BIT) { family = n; break; }
    free(families);
    CHECK(family != UINT32_MAX);
    float priority = 1;
    VkDeviceQueueCreateInfo qi = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    VkDeviceCreateInfo di = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &qi};
    VkDevice device;
    VK(vkCreateDevice(physical, &di, NULL, &device));
    VkQueue queue;
    vkGetDeviceQueue(device, family, 0, &queue);

    const VkDeviceSize size = COUNT * 4 * sizeof(uint32_t);
    VkBufferCreateInfo bi = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = size,
        .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
    VkBuffer buffer;
    VK(vkCreateBuffer(device, &bi, NULL, &buffer));
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(device, buffer, &req);
    VkPhysicalDeviceMemoryProperties memory;
    vkGetPhysicalDeviceMemoryProperties(physical, &memory);
    uint32_t type = UINT32_MAX;
    const VkMemoryPropertyFlags wanted = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    for (uint32_t n = 0; n < memory.memoryTypeCount; ++n)
        if ((req.memoryTypeBits & (1u << n)) && (memory.memoryTypes[n].propertyFlags & wanted) == wanted) { type = n; break; }
    CHECK(type != UINT32_MAX);
    VkMemoryAllocateInfo ai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = req.size, .memoryTypeIndex = type};
    VkDeviceMemory allocation;
    VK(vkAllocateMemory(device, &ai, NULL, &allocation));
    VK(vkBindBufferMemory(device, buffer, allocation, 0));
    uint32_t *data = NULL;
    VK(vkMapMemory(device, allocation, 0, size, 0, (void **)&data));

    VkDescriptorSetLayoutBinding binding = {.binding = 0, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
        .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT};
    VkDescriptorSetLayoutCreateInfo sli = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 1, .pBindings = &binding};
    VkDescriptorSetLayout set_layout;
    VK(vkCreateDescriptorSetLayout(device, &sli, NULL, &set_layout));
    VkPipelineLayoutCreateInfo pli = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1, .pSetLayouts = &set_layout};
    VkPipelineLayout layout;
    VK(vkCreatePipelineLayout(device, &pli, NULL, &layout));
    VkDescriptorPoolSize pool_size = {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1};
    VkDescriptorPoolCreateInfo dpi = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 1, .poolSizeCount = 1, .pPoolSizes = &pool_size};
    VkDescriptorPool descriptors;
    VK(vkCreateDescriptorPool(device, &dpi, NULL, &descriptors));
    VkDescriptorSetAllocateInfo dsi = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = descriptors, .descriptorSetCount = 1, .pSetLayouts = &set_layout};
    VkDescriptorSet set;
    VK(vkAllocateDescriptorSets(device, &dsi, &set));
    VkDescriptorBufferInfo dbi = {.buffer = buffer, .offset = 0, .range = size};
    VkWriteDescriptorSet write = {.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
        .dstSet = set, .dstBinding = 0, .descriptorCount = 1,
        .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &dbi};
    vkUpdateDescriptorSets(device, 1, &write, 0, NULL);
    VkShaderModuleCreateInfo smi = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = (size_t)bytes, .pCode = spirv};
    VkShaderModule shader;
    VK(vkCreateShaderModule(device, &smi, NULL, &shader));
    free(spirv);
    VkCommandPoolCreateInfo cpi = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .queueFamilyIndex = family, .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT};
    VkCommandPool commands;
    VK(vkCreateCommandPool(device, &cpi, NULL, &commands));
    VkCommandBufferAllocateInfo cai = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = commands, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    VkCommandBuffer command;
    VK(vkAllocateCommandBuffers(device, &cai, &command));

    printf("{\"mode\":\"%s\",\"cpu_device_verified\":true,\"invocations\":%d,\"repeats\":%d,\"variants\":[",
           perf && *perf ? perf : "default", COUNT, REPEATS);
    double total_create = 0, total_first = 0, total_repeat = 0;
    for (uint32_t variant = 0; variant < VARIANTS; ++variant) {
        uint32_t rounds = 17 + variant * 32;
        VkSpecializationMapEntry entry = {.constantID = 0, .offset = 0, .size = sizeof(rounds)};
        VkSpecializationInfo spec = {.mapEntryCount = 1, .pMapEntries = &entry,
            .dataSize = sizeof(rounds), .pData = &rounds};
        VkComputePipelineCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
            .layout = layout, .stage = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = shader, .pName = "main", .pSpecializationInfo = &spec}};
        double start = monotonic();
        VkPipeline pipeline;
        VK(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &ci, NULL, &pipeline));
        double create = monotonic() - start;
        VK(vkResetCommandBuffer(command, 0));
        VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
        VK(vkBeginCommandBuffer(command, &begin));
        vkCmdBindPipeline(command, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        vkCmdBindDescriptorSets(command, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, 1, &set, 0, NULL);
        vkCmdDispatch(command, GROUPS, 1, 1);
        VkMemoryBarrier barrier = {.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
            .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT};
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                             0, 1, &barrier, 0, NULL, 0, NULL);
        VK(vkEndCommandBuffer(command));
        VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
            .commandBufferCount = 1, .pCommandBuffers = &command};
        double first = 0, repeated = 0;
        for (unsigned n = 0; n <= REPEATS; ++n) {
            memset(data, 0xcc, (size_t)size);
            start = monotonic();
            VK(vkQueueSubmit(queue, 1, &submit, VK_NULL_HANDLE));
            VK(vkQueueWaitIdle(queue));
            double elapsed = monotonic() - start;
            if (n == 0) first = elapsed; else repeated += elapsed;
            verify(data, rounds);
        }
        total_create += create; total_first += first; total_repeat += repeated;
        printf("%s{\"rounds\":%u,\"create_seconds\":%.9f,\"first_dispatch_seconds\":%.9f,\"repeat_total_seconds\":%.9f}",
               variant ? "," : "", rounds, create, first, repeated);
        fflush(stdout);
        vkDestroyPipeline(device, pipeline, NULL);
    }
    struct rusage usage;
    CHECK(getrusage(RUSAGE_SELF, &usage) == 0);
    printf("],\"create_total_seconds\":%.9f,\"first_dispatch_total_seconds\":%.9f,"
           "\"repeat_total_seconds\":%.9f,\"peak_rss_kib\":%ld,\"all_results_correct\":true}\n",
           total_create, total_first, total_repeat, usage.ru_maxrss);
    vkDestroyCommandPool(device, commands, NULL);
    vkDestroyShaderModule(device, shader, NULL);
    vkDestroyDescriptorPool(device, descriptors, NULL);
    vkDestroyPipelineLayout(device, layout, NULL);
    vkDestroyDescriptorSetLayout(device, set_layout, NULL);
    vkUnmapMemory(device, allocation);
    vkDestroyBuffer(device, buffer, NULL);
    vkFreeMemory(device, allocation, NULL);
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);
    return 0;
}
