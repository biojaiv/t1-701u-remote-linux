# Third-party code and data

The project's own code is under the MIT license (see LICENSE). These parts keep
their own licenses:

| Part | What | License |
|---|---|---|
| `third_party/monocypher-4.0.3/` | Monocypher by Loup Vaillant and contributors (encryption) | CC0-1.0 or BSD-2-Clause, see its `LICENCE.md` |
| `third_party/tinyalsa-1.1.1/` | tinyalsa by The Android Open Source Project and contributors (sound on the tablet) | BSD-3-Clause, see its `NOTICE` |
| `third_party/aosp-4.4.2-headers/` | C headers of Android 4.4.2 by The Android Open Source Project (interface of the tablet's camera driver; only built with the optional Android NDK) | Apache-2.0, see its `NOTICE` |
| `tablet/font_aa.h` | glyph bitmaps rendered from the DejaVu fonts | Bitstream Vera license, DejaVu changes in the public domain, see `third_party/dejavu-fonts/` |

Each folder in `third_party/` has a `README-project.md` (where present) with the
source and checksums.
