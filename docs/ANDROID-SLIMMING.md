# Slimming down Android (removal list)

Android cannot be removed from this tablet: the system partition is write-protected below the
kernel (step P5.4 in [PLAN.md](PLAN.md)). What is possible:

- **Disable** (`pm disable`): the app never starts again and uses no memory; its file in `/system`
  remains. Reversible with `pm enable PACKAGE`.
- **Remove updates** (`pm uninstall` for preinstalled apps that have an update): deletes the newer
  version in `/data/app`; the factory version in `/system` remains. Reversible only by updating
  again (the Play Store is disabled afterwards).
- **Clear data** (`pm clear`): the app's accounts, settings and cache are **gone for good**.

Space is freed only on `/data`: estimated **about 300 MB** (updates 85 MB + libraries 18 MB, app
data around 200 MB, parts of the `dalvik-cache`). `/system` does not get smaller.

## Stays enabled

Needed for booting Android, root, the autostart app and a usable way back to Android:

`android`, `androidhwext`, `com.android.systemui`, `com.android.keyguard`, `com.android.settings`,
`com.android.providers.settings`, `com.android.phone`, `com.android.providers.telephony`,
`com.android.shell`, `com.android.defcontainer`, `com.android.packageinstaller`,
`com.android.inputdevices`, `com.android.keychain`, `com.android.certinstaller`,
`com.android.externalstorage`, `com.android.providers.media`, `com.android.providers.downloads`,
`com.android.providers.contacts`, `com.android.providers.applications`,
`com.android.providers.userdictionary`, `com.android.location.fused`,
`com.android.inputmethod.latin` (Android keyboard for emergencies), `com.huawei.android.launcher`
(Android launcher for emergencies), `com.huawei.android.internal.app`,
`com.huawei.systemmanager`, `com.huawei.powergenie`, `com.huawei.policy`,
`com.kingroot.kinguser` (root), `local.tablet.autostart`, `com.sprd.bootfailselect`,
`com.android.backupconfirm`, `com.android.sharedstoragebackup`, `com.android.onetimeinitializer`,
`com.android.proxyhandler`, `com.android.vpndialogs`, `com.android.pacprocessor`,
`com.android.stk`, `com.android.smspush`, `org.simalliance.openmobileapi.service`.

## Remove updates, disable, clear data

`com.google.android.gms` (Play services), `com.android.vending` (Play Store),
`com.google.android.inputmethod.latin` (Google keyboard).

## Disable and clear data

Google: `com.google.android.gsf`, `com.google.android.gsf.login`, `com.google.android.youtube`,
`com.google.android.gm`, `com.google.android.talk`, `com.google.android.apps.plus`,
`com.google.android.music`, `com.google.android.videos`, `com.google.android.apps.books`,
`com.google.android.apps.magazines`, `com.google.android.apps.maps`, `com.google.android.street`,
`com.google.android.apps.docs`, `com.google.android.play.games`,
`com.google.android.googlequicksearchbox`, `com.google.android.feedback`,
`com.google.android.backuptransport`, `com.google.android.configupdater`,
`com.google.android.partnersetup`, `com.google.android.setupwizard`,
`com.google.android.onetimeinitializer`, `com.google.android.syncadapters.calendar`,
`com.google.android.syncadapters.contacts`, `com.google.android.apps.cloudprint`,
`com.google.android.apps.uploader`.

Android and Huawei: `com.android.chrome`, `com.android.browser`, `com.android.browser.provider`,
`com.android.email`, `com.android.exchange`, `com.android.calendar`, `com.android.calculator2`,
`com.android.deskclock`, `com.android.gallery3d`, `com.android.mediacenter`, `com.android.musicfx`,
`com.android.soundrecorder`, `com.android.htmlviewer`, `com.android.documentsui`,
`com.android.dreams.basic`, `com.android.dreams.phototable`, `com.android.galaxy4`,
`com.android.magicsmoke`, `com.android.musicvis`, `com.android.wallpaper.holospiral`,
`com.android.wallpaper.livepicker`, `com.android.wallpaper`, `com.android.wallpapercropper`,
`com.android.printspooler`, `com.android.contacts`, `com.android.incallui`,
`com.android.bluetooth`, `com.example.android.notepad`, `com.svox.pico`, `cn.wps.moffice_eng`,
`com.nuance.swype.emui`, `com.huawei.android.totemweather`, `com.huawei.android.thememanager`,
`com.huawei.hidisk`, `com.huawei.hwvplayer`, `com.huawei.KoBackup`, `com.huawei.camera`,
`com.huawei.floatMms`, `com.android.huawei.countlapsetime`, `com.android.huawei.projectmenu`,
`com.android.hwmirror`, `com.huawei.android.wfdft`, `com.huawei.android.hwouc`,
`com.huawei.android.apkbatchinstall`, `com.huawei.installApkAccordSimCard`,
`com.huawei.EmergencyData`, `com.huawei.dataswitchmanager`, `com.huawei.omacp`,
`com.huawei.mmitest`, `com.huawei.mmifunction`, `com.huawei.privacymode`, `com.huawei.bluetooth`,
`com.huawei.wifieapsimplmn`, `com.sprd.engineermode`.

## Consequences

- Android then has no browser, no Google services, no Play Store, and no camera, music or gallery
  app. Android still boots, with Settings and its launcher.
- A Google account on the tablet is signed out afterwards (data cleared).
- To undo: `pm enable PACKAGE` per app; cleared data and removed updates do not come back.
