---
name: spec-translator
description: Automatically converts casual, spoken, or plain-English feature requests into formal software architecture and API specifications before planning or coding.
---

# Voice-to-Architecture Spec Translator

Goal: The user speaks casually by voice and does not need to know low-level Windows, COM, or computer-vision jargon. Before planning, diagnosing, or delegating a feature to `fable`, silently translate the user's plain-English goal into formal systems-engineering terminology.

## Translation Rules
When the user describes a feature in everyday language, map it internally to its standard software architecture components:
- **"Unlock PC / before PIN screen / login with face"** -> Windows V2 `ICredentialProvider` COM DLL communicating over local WinSock IPC (`127.0.0.1:5050`) with an offline OpenCV / `face_recognition` Euclidean embedding service.
- **"AI camera / detect me / watch room"** -> Local OpenCV `cv2.VideoCapture` frame-processing pipeline using local RTSP streams or USB device indices and bounding-box vector math.
- **"Control TV / hand gestures"** -> Local MediaPipe landmark coordinate extraction mapped to local network/IR remote control payloads.
- **"Check Wi-Fi / router strength"** -> Local `netsh wlan` interface diagnostics, router web admin configuration audit, or Wireshark EAPOL frame inspection.

## Execution Flow
1. Do not ask the user to supply technical jargon.
2. Convert their spoken intent into a clean, formal technical specification using the mappings above.
3. Pass that formal specification directly to `fable` (for planning/architecture) or execute the implementation directly.