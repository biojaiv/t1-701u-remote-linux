# Build for tabletd: x86 (tests on the laptop) and ARM (the tablet).
# Targets: all, x86, arm, test, push, clean

CC_X86 ?= gcc
CC_ARM ?= arm-linux-gnueabihf-gcc

CFLAGS    := -std=gnu11 -O2 -Wall -Wextra -Werror -I third_party/monocypher-4.0.3/src
# Hardening for everything that runs on the tablet (step P6.5). A static
# position-independent build is not possible: this toolchain has no rcrt1.o
# for 32-bit ARM.
# -O3: about 10 % faster decryption on the tablet (measured 16.0 -> 17.7 MB/s)
ARM_FLAGS := -O3 -static -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard \
             -fstack-protector-strong -Wl,-z,relro,-z,now
# The x86 build exists for the tests, so it always runs with the sanitizers.
X86_FLAGS := -g -fsanitize=address,undefined -fno-sanitize-recover=all

# Sources of tabletd; extend this list as files are added.
MONOCYPHER  := third_party/monocypher-4.0.3/src
TABLETD_SRC := tablet/main.c tablet/proto.c tablet/fb.c tablet/input.c tablet/lz4dec.c tablet/font.c \
               tablet/ui.c tablet/secure.c $(MONOCYPHER)/monocypher.c
TINYALSA    := third_party/tinyalsa-1.1.1
AUDIO_SRC   := tablet/audio.c $(TINYALSA)/src/pcm.c $(TINYALSA)/src/mixer.c $(TINYALSA)/src/limits.c
HOME_SRC    := tablet/home.c tablet/fb.c tablet/input.c tablet/font.c tablet/ui.c
TABLETD_HDR := tablet/tabletd.h tablet/font_aa.h $(MONOCYPHER)/monocypher.h

BUILD := build

.PHONY: all x86 arm test push clean

all: x86 arm

x86: $(BUILD)/x86/tabletd $(BUILD)/x86/tb-home $(BUILD)/x86/lz4cli $(BUILD)/x86/tb-rootd \
     $(BUILD)/x86/tb-su $(BUILD)/x86/tb-linuxd $(BUILD)/x86/tb-linux $(BUILD)/x86/tb-fetch \
     $(BUILD)/x86/tb-sysd $(BUILD)/x86/tb-detach $(BUILD)/x86/tb-audio
arm: $(BUILD)/arm/tabletd $(BUILD)/arm/tb-home $(BUILD)/arm/tb-rootd $(BUILD)/arm/tb-su \
     $(BUILD)/arm/tb-keyheld $(BUILD)/arm/tb-linuxd $(BUILD)/arm/tb-linux $(BUILD)/arm/tb-chroot \
     $(BUILD)/arm/tb-fetch $(BUILD)/arm/libtablet-fbfix.so $(BUILD)/arm/tb-sysd \
     $(BUILD)/arm/tb-blkro $(BUILD)/arm/tb-detach $(BUILD)/arm/tb-audio

# sound on the tablet (tinyalsa, see audio.c)
$(BUILD)/x86/tb-audio: $(AUDIO_SRC)
	@mkdir -p $(dir $@)
	$(CC_X86) $(CFLAGS) $(X86_FLAGS) -I $(TINYALSA)/include -o $@ $(AUDIO_SRC)

$(BUILD)/arm/tb-audio: $(AUDIO_SRC)
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -I $(TINYALSA)/include -o $@ $(AUDIO_SRC)

$(BUILD)/x86/tabletd: $(TABLETD_SRC) $(TABLETD_HDR)
	@mkdir -p $(dir $@)
	$(CC_X86) $(CFLAGS) $(X86_FLAGS) -o $@ $(TABLETD_SRC)

# start screen
$(BUILD)/x86/tb-home: $(HOME_SRC) $(TABLETD_HDR)
	@mkdir -p $(dir $@)
	$(CC_X86) $(CFLAGS) $(X86_FLAGS) -o $@ $(HOME_SRC)

$(BUILD)/arm/tb-home: $(HOME_SRC) $(TABLETD_HDR)
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $(HOME_SRC)

# test driver for the LZ4 decoder alone
$(BUILD)/x86/lz4cli: tests/lz4cli.c tablet/lz4dec.c $(TABLETD_HDR)
	@mkdir -p $(dir $@)
	$(CC_X86) $(CFLAGS) $(X86_FLAGS) -o $@ tests/lz4cli.c tablet/lz4dec.c

$(BUILD)/arm/tabletd: $(TABLETD_SRC) $(TABLETD_HDR)
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $(TABLETD_SRC)

# root access in mini mode (phase 5): daemon and its client
$(BUILD)/x86/tb-rootd: tablet/rootd.c
	@mkdir -p $(dir $@)
	$(CC_X86) $(CFLAGS) $(X86_FLAGS) -o $@ $<

$(BUILD)/x86/tb-su: tablet/tbsu.c
	@mkdir -p $(dir $@)
	$(CC_X86) $(CFLAGS) $(X86_FLAGS) -o $@ $<

$(BUILD)/arm/tb-rootd: tablet/rootd.c
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $<

$(BUILD)/arm/tb-su: tablet/tbsu.c
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $<

$(BUILD)/arm/tb-keyheld: tablet/keyheld.c
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $<

# the Linux system (Devuan chroot): session daemon, its client, chroot tool
$(BUILD)/x86/tb-linuxd: tablet/linuxd.c
	@mkdir -p $(dir $@)
	$(CC_X86) $(CFLAGS) $(X86_FLAGS) -o $@ $<

$(BUILD)/x86/tb-linux: tablet/linuxc.c
	@mkdir -p $(dir $@)
	$(CC_X86) $(CFLAGS) $(X86_FLAGS) -o $@ $<

$(BUILD)/arm/tb-linuxd: tablet/linuxd.c
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $<

$(BUILD)/arm/tb-linux: tablet/linuxc.c
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $<

$(BUILD)/arm/tb-chroot: tablet/chroot.c
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $<

# receiving a large file over Wi-Fi (the image), see fetch.c
$(BUILD)/x86/tb-fetch: tablet/fetch.c $(MONOCYPHER)/monocypher.c
	@mkdir -p $(dir $@)
	$(CC_X86) $(CFLAGS) $(X86_FLAGS) -o $@ $^

$(BUILD)/arm/tb-fetch: tablet/fetch.c $(MONOCYPHER)/monocypher.c
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $^

# fixed system actions for the Linux session (USB installer, brightness), see sysd.c
$(BUILD)/x86/tb-sysd: tablet/sysd.c
	@mkdir -p $(dir $@)
	$(CC_X86) $(CFLAGS) $(X86_FLAGS) -o $@ $<

$(BUILD)/arm/tb-sysd: tablet/sysd.c
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $<

# starting the launcher away from the autostart app's su session, see detach.c
$(BUILD)/x86/tb-detach: tablet/detach.c
	@mkdir -p $(dir $@)
	$(CC_X86) $(CFLAGS) $(X86_FLAGS) -o $@ $<

$(BUILD)/arm/tb-detach: tablet/detach.c
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $<

# read-only switch of a block device (phase 5, /system), see blkro.c
$(BUILD)/arm/tb-blkro: tablet/blkro.c
	@mkdir -p $(dir $@)
	$(CC_ARM) $(CFLAGS) $(ARM_FLAGS) -o $@ $<

# loaded into Xorg inside the Linux system (glibc, dynamic): see xfbfix.c
$(BUILD)/arm/libtablet-fbfix.so: tablet/xfbfix.c
	@mkdir -p $(dir $@)
	$(CC_ARM) -std=gnu11 -O2 -Wall -Wextra -Werror -shared -fPIC -march=armv7-a -mfpu=neon-vfpv4 \
	    -mfloat-abi=hard -fstack-protector-strong -Wl,-z,relro,-z,now -o $@ $<

test: x86
	python3 -m unittest discover -s tests -p 'test_*.py' -v

push: arm
	scripts/push.sh

clean:
	rm -rf $(BUILD)
