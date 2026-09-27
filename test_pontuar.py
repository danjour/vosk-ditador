"""Check da heuristica pontuar() do vosk_global."""
import os
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "vosk_global.py"), encoding="utf-8").read()
ns = {"__file__": os.path.join(os.path.dirname(os.path.abspath(__file__)), "vosk_global.py")}
exec(src[:src.index('print("modelo:"')], ns)
pontuar = ns["pontuar"]
casos = {
    "quem vai na reunião amanhã": "Quem vai na reunião amanhã?",
    "onde fica a chave do carro": "Onde fica a chave do carro?",
    "como faço para exportar o relatório": "Como faço para exportar o relatório?",
    "preciso enviar o relatório hoje": "Preciso enviar o relatório hoje.",
    "me liga quando chegar": "Me liga quando chegar.",
}
ok = True
for entrada, esperado in casos.items():
    got = pontuar(entrada)
    if got != esperado:
        ok = False
        print(f"FAIL {entrada!r} -> {got!r} (esperado {esperado!r})")
print("TUDO-OK" if ok else "TEM-FALHA")
raise SystemExit(0 if ok else 1)
