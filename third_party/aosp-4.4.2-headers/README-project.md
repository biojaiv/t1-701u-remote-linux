# AOSP 4.4.2 headers (camera HAL interface)

C headers of Android 4.4.2, unchanged, so that `tablet/camera/camera.c` (tb-camera)
can load the tablet's own camera driver (`camera.sc8830.so`, a camera HAL v1
module) without Android's camera service. Only the interface definitions; no
Android code is built into the project.

- Source: https://android.googlesource.com, tag `android-4.4.2_r1`, fetched as
  `.../+/refs/tags/android-4.4.2_r1/<path>?format=TEXT` (base64)
  - `platform/hardware/libhardware`: `include/hardware/{hardware,camera_common,camera,gralloc,fb}.h`
    → `hardware/`
  - `platform/system/core`: `include/system/{window,camera,graphics}.h` → `system/`,
    `include/cutils/native_handle.h` → `cutils/`, `include/sync/sync.h` → `sync/`
  - `NOTICE`: `platform/hardware/libhardware/NOTICE` at the same tag (the Apache
    License 2.0 text)
- Licence: Apache License 2.0, copyright The Android Open Source Project (see each
  file's header and `NOTICE`)
- SHA-256 of the files as kept here:

```
dc40921bf7e369619be1fe67b8b4e5280bcc0b402c71107050f8546bb4c745e7  cutils/native_handle.h
12d4f12fec422a734d7cb0bc1cbaab5616bb9b7b33df61aea487f0a17616a7b2  hardware/camera_common.h
842e562f576fe48bf789c756b4b5177b3b993c29128924147a3383e90c85d03f  hardware/camera.h
6f910834624f99213eca5531fd3721844332e3c61e6dc0a218bd8d5a4d147f60  hardware/fb.h
2710d4a267cd0039bd709bd1491a102c77704dea8d4150ac359a5bc9d22118b2  hardware/gralloc.h
fe1267df776ea070276442669e65ef475addebb6a6761616c681970552e837e6  hardware/hardware.h
38751245389e1e23f73e6f5384b5cbe7fa972cc4410c5adc9c04b082a0b9561a  NOTICE
0765f48635d3062605efd60481beed9a05ba777dc7c546083f3a7b752e0447a7  sync/sync.h
1b26b9bc6a31463ba0f64591ac812ba679ec9019e18e559357aded7c77f62c81  system/camera.h
a535cb046845b27aedb40db351309a83e49223a0da451a5eed499d2277a41056  system/graphics.h
cbab76730bc5de57752a94895026137c062996ee16589c808a277cc9afa5afca  system/window.h
```
