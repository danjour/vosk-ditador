"""Baixa modelo Vosk PT p/ ./models (nunca commitar modelo). Uso: python setup_model.py [--big]"""
import os, shutil, sys, urllib.request, zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(_HERE, "models")
SMALL = ("vosk-model-small-pt-0.3",
         "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip")
BIG = ("vosk-model-pt-fb-v0.1.1-20220516_2113",
       "https://alphacephei.com/vosk/models/vosk-model-pt-fb-v0.1.1-20220516_2113.zip")

def fetch(name, url):
    dest = os.path.join(MODELS, name)
    if os.path.isdir(dest):
        print(name, "já existe, pulando")
        return
    os.makedirs(MODELS, exist_ok=True)
    zf = os.path.join(MODELS, name + ".zip")
    print("baixando", name, "...", flush=True)
    urllib.request.urlretrieve(url, zf)
    with zipfile.ZipFile(zf) as z:
        z.extractall(MODELS)
    os.remove(zf)
    rs = os.path.join(dest, "rescore")  # ponytail: G.carpa do fb quebra o loader; rescore eh opcional
    if os.path.isdir(rs):
        shutil.move(rs, rs + ".off")
        print("rescore desativado (G.carpa incompatível)")
    print("ok:", dest)

fetch(*SMALL)
if "--big" in sys.argv:
    fetch(*BIG)
