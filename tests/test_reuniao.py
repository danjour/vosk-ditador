"""Testes das funções puras do modo reunião (sem GPU, sem rede, sem áudio real).
Roda na raiz do projeto: python tests/test_reuniao.py  (ou python -m unittest discover tests)"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reuniao
import reuniao_transcrever as rt


class FakeWav:  # grava os writeframes p/ conferir quantas amostras saíram
    def __init__(self):
        self.frames = []

    def writeframes(self, b):
        self.frames.append(b)

    def total(self):
        return sum(len(b) for b in self.frames) // 2


class TestRelogioDeParede(unittest.TestCase):
    """O conserto da dessincronia: tempo morto vira silêncio no mesmo relógio."""

    def setUp(self):
        reuniao.S["t0"] = 0.0

    def test_gravacao_continua_nao_preenche(self):
        w, rel = FakeWav(), {"escrito": 0.0}
        reuniao._grava_rel(w, b"\x01\x00" * 8000, 8000, rel, agora=1.0)
        self.assertEqual(w.total(), 8000)
        self.assertAlmostEqual(rel["escrito"], 1.0)

    def test_queda_de_4s_vira_silencio(self):
        w, rel = FakeWav(), {"escrito": 0.0}
        reuniao._grava_rel(w, b"\x01\x00" * 8000, 8000, rel, agora=1.0)  # [0,1] áudio
        reuniao._grava_rel(w, b"\x01\x00" * 8000, 8000, rel, agora=5.0)  # caiu 1→4
        self.assertAlmostEqual(w.total() / 8000, 5.0, delta=0.001)  # 1+3 silêncio+1

    def test_deriva_abaixo_da_tolerancia_nao_preenche(self):
        w, rel = FakeWav(), {"escrito": 0.0}
        reuniao._grava_rel(w, b"\x01\x00" * 8000, 8000, rel, agora=0.1)
        self.assertEqual(w.total(), 8000)

    def test_canais_em_taxas_diferentes_ficam_alinhados(self):
        # mic a 8k e sistema a 16k, ambos com queda: durações em segundos têm que bater
        wm, rm = FakeWav(), {"escrito": 0.0}
        reuniao._grava_rel(wm, b"\x01\x00" * 8000, 8000, rm, agora=6.0)
        ws, rs = FakeWav(), {"escrito": 0.0}
        reuniao._grava_rel(ws, b"\x01\x00" * 16000, 16000, rs, agora=6.0)
        self.assertAlmostEqual(wm.total() / 8000, ws.total() / 16000, delta=0.001)


class TestAgrupa(unittest.TestCase):
    TURNOS = [(0.0, 10.0, "SPEAKER_00"), (12.0, 20.0, "SPEAKER_01")]

    def test_palavra_orfa_cai_no_vizinho_mais_proximo(self):
        evs = rt._agrupa([(10.8, 11.0, " oi")], self.TURNOS)  # entre os 2 turnos
        self.assertEqual(evs[0][2], "Remoto 1")  # SPEAKER_00 (0.8s) < SPEAKER_01 (1.0s)

    def test_palavra_dentro_do_turno_vai_pro_dono(self):
        evs = rt._agrupa([(13.0, 13.2, " olá")], self.TURNOS)
        self.assertEqual(evs[0][2], "Remoto 2")  # SPEAKER_01 fala 8s < SPEAKER_00 10s → nº 2

    def test_sem_turnos_vira_remotos(self):
        evs = rt._agrupa([(0.0, 0.5, " olá")], None)
        self.assertEqual(evs[0][2], "Remotos")

    def test_palavras_do_mesmo_falante_fundem(self):
        evs = rt._agrupa([(0.0, 0.3, " bom"), (0.5, 0.8, " dia")], self.TURNOS)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0][3], "bom dia")

    def test_orfa_fora_do_alcance_permanece_remotos(self):
        evs = rt._agrupa([(30.0, 30.2, " ???")], self.TURNOS)
        self.assertEqual(evs[0][2], "Remotos")


class TestMaisProximo(unittest.TestCase):
    def test_dentro_do_turno_retorna_na_hora(self):
        self.assertEqual(rt._mais_proximo(5.0, 5.5, [(0, 10, "A")]), "A")

    def test_alem_do_alcance_retorna_none(self):
        self.assertIsNone(rt._mais_proximo(10.0, 10.5, [(0, 1, "A")]))


class TestMesclaEFormato(unittest.TestCase):
    def test_mesma_pessoa_com_lacuna_curta_funde(self):
        evs = [(0, 2, "Você", "a"), (2.5, 4, "Você", "b"), (10, 12, "Remotos", "c")]
        saida = rt._mescla(evs)
        self.assertEqual(len(saida), 2)
        self.assertEqual(saida[0][3], "a b")

    def test_pessoas_diferentes_nao_fundem(self):
        saida = rt._mescla([(0, 2, "Você", "a"), (2.5, 4, "Remotos", "b")])
        self.assertEqual(len(saida), 2)

    def test_hhmmss(self):
        self.assertEqual(rt._hhmmss(754), "00:12:34")
        self.assertEqual(rt._hhmmss(3661), "01:01:01")
        self.assertEqual(rt._hhmmss(-3), "00:00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
