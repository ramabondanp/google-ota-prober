# CONFIG SUPPORT
<details>
  <summary>List of supported Transsion devices</summary>

## PHANTOM SERIES
* TECNO PHANTOM X2 (AD8)
* TECNO PHANTOM X2 Pro (AD9)
* TECNO PHANTOM V Fold 5G (AD10)
* TECNO PHANTOM V Flip 5G (AD11)
* TECNO PHANTOM V Fold2 5G (AE10)
* TECNO PHANTOM V Flip2 5G (AE11)

## CAMON SERIES
* TECNO CAMON 20 (CK6)
* TECNO CAMON 20 Pro (CK7n)
* TECNO CAMON 20 Pro 5G (CK8n)
* TECNO CAMON 20s Pro 5G (CK8nB)
* TECNO CAMON 30 4G (CL6)
* TECNO CAMON 30 5G (CL7)
* TECNO CAMON 30 Pro 5G (CL8)
* TECNO CAMON 30 Premier 5G (CL9)
* TECNO CAMON 30S (CLA5)
* TECNO CAMON 30S Pro (CLA6)
* TECNO CAMON 40 4G (CM5)
* TECNO CAMON 40 Pro 4G (CM6)
* TECNO CAMON 40 Pro 5G (CM7)
* TECNO CAMON 40 Premier 5G (CM8)

## SPARK SERIES
* TECNO SPARK Go 1 (KL4)
* TECNO SPARK 20 (KJ5)
* TECNO SPARK 20 Pro (KJ6)
* TECNO SPARK 20 Pro+ (KJ7)
* TECNO SPARK 20 Pro 5G (KJ8)
* TECNO SPARK 30C (KL5)
* TECNO SPARK 30 4G (KL6)
* TECNO SPARK 30 Pro (KL7)
* TECNO SPARK 30 5G (KL8)
* TECNO SPARK 30C 5G (KL8H)

## POVA SERIES
* TECNO POVA Neo 3 (LH6n)
* TECNO POVA 5 (LH7n)
* TECNO POVA 5 Pro (LH8n)
* TECNO POVA 6 Neo (LI6)
* TECNO POVA 6 (LI7)
* TECNO POVA 6 Pro (LI9)

## MEGAPAD
* TECNO MEGAPAD 11 (T1101)

## ITEL
* itel A80 (A671LC)
* itel P55 5G (P661N)
* itel P65 (P671L)
* itel RS4 (S666LN)
* itel S25 (S685LN)
* itel S25 Ultra (S686LN)

## XPAD
* Infinix XPAD (X1101)

## HOT SERIES
* Infinix HOT 40i (X6528) (X6528B)
* Infinix HOT 40 (X6836)
* Infinix HOT 40 Pro (X6837)
* Infinix HOT 50i (X6531) (X6531B)
* Infinix HOT 50 5G (X6720B)
* Infinix HOT 50 Pro+ (X6880)
* Infinix HOT 50 Pro (X6881)
* Infinix HOT 50 (X6882)

## ZERO SERIES
* Infinix ZERO 30 5G (X6731)
* Infinix ZERO 30 4G (X6731B)
* Infinix ZERO 40 4G (X6860)
* Infinix ZERO 40 5G (X6861)
* Infinix ZERO Flip (X6962)

## GT SERIES
* Infinix GT 10 Pro (X6739)
* Infinix GT 20 Pro (X6871)

## NOTE SERIES
* Infinix NOTE 30 VIP (X6710)
* Infinix NOTE 30 5G (X6711)
* Infinix NOTE 30 (Helio G85) (X6716B)
* Infinix NOTE 30 Pro (X678B)
* Infinix NOTE 30 (X6833B)
* Infinix NOTE 40X 5G (X6838)
* Infinix NOTE 40 Pro (X6850)
* Infinix NOTE 40S (X6850B)
* Infinix NOTE 40 Pro 5G (X6851)
* Infinix NOTE 40 Pro+ 5G (X6851B)
* Infinix NOTE 40 5G (X6852)
* Infinix NOTE 40 (X6853)
* Infinix NOTE 50 Pro 4G (X6855)
* Infinix NOTE 50 Pro+ 5G (X6856)
* Infinix NOTE 50 4G (X6858)
</details>

# Google OTA prober

This program is designed to obtain URLs to over-the-air (OTA) update packages from Google's servers for a specified device.

## Requirements
* Python 3
* Build fingerprint of your stock ROM

## How to use

You must install dependencies before using the tool: `python -m pip install -r requirements.txt`

### Option 1: Using a terminal
There are three ways to get the URL, which are listed here:
```
python probe.py --fingerprint <fingerprint>   # Skips reading config.yml entirely.
python probe.py --config <filename>           # Reads a custom YML file (same format as config.yml)
python probe.py                               # Reads config.yml
```

If you wish to download the OTA file, pass `--download` as an argument on your terminal.

### Option 2: Using a graphical interface
This option requires installing the `flet` and `markdownify` modules to proceed. You must have the fingerprint for your device. The model code is optional, but encouraged.

You can run the GUI with `python gui.py`.

## Limitations
* This only works for devices that use Google's OTA update servers.
* The prober can only get the latest OTA update package that works on the build specified in `config.yml`.
* Unless it is a major Android upgrade (11 -> 12), the prober will only get links for incremental OTA packages.

## References
1. https://github.com/MCMrARM/Google-Play-API/blob/master/proto/gsf.proto
2. https://github.com/microg/GmsCore/blob/master/play-services-core-proto/src/main/proto/checkin.proto
3. https://chromium.googlesource.com/chromium/chromium/+/trunk/google_apis/gcm/protocol/android_checkin.proto
4. https://github.com/p1gp1g/fp3_get_ota_url
