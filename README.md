# CONFIG SUPPORT
* TECNO PHANTOM V Fold2 5G (AE10)
* TECNO PHANTOM V Flip2 5G (AE11)
* TECNO CAMON 30 Pro 5G (CL8)
* TECNO SPARK 20 Pro+ (KJ7)
* TECNO POVA 5 (LH7n)
* TECNO POVA 6 (LI7)
* Infinix ZERO 30 4G (X6731B)
* Infinix GT 10 Pro (X6739)
* Infinix NOTE 12 2023 (X676C)
* Infinix NOTE 30 Pro (X678B)
* Infinix NOTE 30 (X6833B)
* Infinix NOTE 40 Pro 4G (X6850)
* Infinix NOTE 40 4G (X6853)
* Infinix ZERO 40 5G (X6861)
* Infinix GT 20 Pro (X6871)

# Google OTA prober

This program is designed to obtain URLs to over-the-air (OTA) update packages from Google's servers for a specified device.

## Requirements
* Python 3
* Build fingerprint of your stock ROM

## How to use
1. Install needed dependencies: `python -m pip install -r requirements.txt`
2. Modify `config.yml` correctly, as described in the file itself.
3. `python probe.py`

If you wish to download the OTA file, pass `--download` as an argument on your terminal.

## Limitations
* This only works for devices that use Google's OTA update servers.
* The prober can only get the latest OTA update package that works on the build specified in `config.yml`.
* Unless it is a major Android upgrade (11 -> 12), the prober will only get links for incremental OTA packages.

## References
1. https://github.com/MCMrARM/Google-Play-API/blob/master/proto/gsf.proto
2. https://github.com/microg/GmsCore/blob/master/play-services-core-proto/src/main/proto/checkin.proto
3. https://chromium.googlesource.com/chromium/chromium/+/trunk/google_apis/gcm/protocol/android_checkin.proto
4. https://github.com/p1gp1g/fp3_get_ota_url
