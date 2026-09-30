"""Baixa modelo Vosk PT p/ ./models (nunca commitar modelo). Uso: python setup_model.py [--big]

O alphacephei limita ~14 KB/s por conexao: o download fatia o zip em N
pedaços paralelos (Accept-Ranges) e junta no fim. Progresso mostra MB e %.
"""
import os, shutil, socket, sys, threading, time, urllib.request, zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(_HERE, "models")
SMALL = ("vosk-model-small-pt-0.3",
         "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip")
BIG = ("vosk-model-pt-fb-v0.1.1-20220516_2113",
       "https://alphacephei.com/vosk/models/vosk-model-pt-fb-v0.1.1-20220516_2113.zip")

def _mb(n):
    return f"{n/1e6:.1f} MB"

def _tree_mb(path):
    return sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(path) for f in fs)

def _seg_get(i, s, e, url, td):
    req = urllib.request.Request(url, headers={"Range": f"bytes={s}-{e}"})
    with urllib.request.urlopen(req, timeout=30) as r, \
         open(os.path.join(td, f"p{i}"), "wb") as f:
        shutil.copyfileobj(r, f)

def fetch(name, url):
    dest = os.path.join(MODELS, name)
    if os.path.isdir(dest):
        print(name, f"já existe ({_mb(_tree_mb(dest))}), pulando")
        return
    os.makedirs(MODELS, exist_ok=True)
    socket.setdefaulttimeout(30)  # conexao morta erra em 30s em vez de travar calada
    total = int(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"),
                                       timeout=30).headers["Content-Length"])
    n = 32 if total > 100_000_000 else 16  # mais conexoes = mais throughput
    seg = total // n
    spans = [(i * seg, (i + 1) * seg - 1) for i in range(n)]
    spans[-1] = (spans[-1][0], total - 1)
    td = os.path.join(MODELS, name + ".parts")
    os.makedirs(td, exist_ok=True)
    print(f"baixando {name} em {n} conexões ({_mb(total)})...", flush=True)
    ok = False
    for _rodada in range(3):  # reconecta so os pedaços que falharem
        pth = [os.path.join(td, f"p{i}") for i in range(n)]
        pend = [i for i, (s, e) in enumerate(spans)
                if not os.path.exists(pth[i]) or os.path.getsize(pth[i]) != e - s + 1]
        ths = [threading.Thread(target=_seg_get, args=(i, spans[i][0], spans[i][1], url, td),
                                daemon=True) for i in pend]
        for t in ths:
            t.start()
        while any(t.is_alive() for t in ths):
            done = sum(os.path.getsize(p) if os.path.exists(p) else 0 for p in pth)
            print(f"\r{_mb(done)}/{_mb(total)} ({100*done/total:3.0f}%)",
                  end="", flush=True)
            time.sleep(0.5)
        ok = all(os.path.getsize(p) == e - s + 1 for p, (s, e) in zip(pth, spans))
        if ok:
            break
    print()
    if not ok:
        raise SystemExit("download incompleto após 3 rodadas — rode de novo")
    zf = os.path.join(MODELS, name + ".zip")
    with open(zf, "wb") as out:
        for p in [os.path.join(td, f"p{i}") for i in range(n)]:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out)
    with zipfile.ZipFile(zf) as z:  # extractall valida o CRC de cada arquivo
        z.extractall(MODELS)
    os.remove(zf)
    shutil.rmtree(td)
    rs = os.path.join(dest, "rescore")  # ponytail: G.carpa do fb quebra o loader; rescore eh opcional
    if os.path.isdir(rs):
        shutil.move(rs, rs + ".off")
        print("rescore desativado (G.carpa incompatível)")
    print(f"ok: {dest} ({_mb(_tree_mb(dest))} extraído)")

fetch(*SMALL)
if "--big" in sys.argv:
    fetch(*BIG)
