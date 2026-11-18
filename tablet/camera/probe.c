/*
 * tb-camprobe - lists what Android's camera HAL module on the tablet reports
 * (module and device API versions, cameras, facing, orientation). Loads the
 * module only; no camera is opened. Built with the Android NDK (bionic),
 * because the module is a bionic library.
 */
#include <dlfcn.h>
#include <stdio.h>

#include <hardware/camera_common.h>
#include <hardware/hardware.h>

typedef int (*get_module_fn)(const char *id, const struct hw_module_t **module);

int main(void)
{
    void *lib = dlopen("libhardware.so", RTLD_NOW);
    get_module_fn get_module = lib ? (get_module_fn)dlsym(lib, "hw_get_module") : NULL;
    const struct hw_module_t *mod = NULL;
    const camera_module_t *cam;
    int n;

    if (!get_module) {
        fprintf(stderr, "tb-camprobe: no libhardware: %s\n", dlerror());
        return 1;
    }
    if (get_module(CAMERA_HARDWARE_MODULE_ID, &mod) != 0 || !mod) {
        fprintf(stderr, "tb-camprobe: no camera module\n");
        return 1;
    }
    cam = (const camera_module_t *)mod;
    printf("module: %s by %s, id %s\n", mod->name, mod->author, mod->id);
    printf("module api 0x%04x, hal api 0x%04x\n", mod->module_api_version, mod->hal_api_version);
    n = cam->get_number_of_cameras();
    printf("cameras: %d\n", n);
    for (int i = 0; i < n; i++) {
        struct camera_info info = { 0 };

        if (cam->get_camera_info(i, &info) != 0) {
            printf("camera %d: no info\n", i);
            continue;
        }
        printf("camera %d: facing %s, orientation %d, device api 0x%04x\n", i,
               info.facing == CAMERA_FACING_BACK ? "back" : "front", info.orientation,
               mod->module_api_version >= CAMERA_MODULE_API_VERSION_2_0 ? info.device_version
                                                                     : 0x100);
    }
    return 0;
}
