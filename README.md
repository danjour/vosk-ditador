# vosk-ditador — ditado offline em PT (Vosk)

```powershell
pip install -r requirements.txt
python setup_model.py        # small 31MB; --big p/ fb 1.6GB (melhor precisão)
python vosk_global.py        # Ctrl+Alt+E ouve/pausa em qualquer app, Ctrl+Alt+Q sai
python vosk_live.py          # só transcreve no console
python test_pontuar.py       # check da heurística de "?"
```

Env: `VOSK_MODEL` (pasta do modelo), `VOSK_DEVICE` (id sounddevice, default 21 = AirPods WASAPI 16k).
Modelo nunca vai pro git (ver `.gitignore`).
