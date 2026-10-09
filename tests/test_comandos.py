"""Comandos de voz: wake-strip, match local, JEV e orquestração. Offline."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from unittest import mock
import requests
import comandos


class TestWake(unittest.TestCase):
    def test_pontuado_com_maiuscula_e_ponto(self):
        self.assertEqual(comandos.tira_wake("Computador, pausar música."), "pausar música.")

    def test_sem_wake_devolve_none(self):
        self.assertIsNone(comandos.tira_wake("preciso comprar pão."))

    def test_wake_customizado(self):
        self.assertEqual(comandos.tira_wake("Jarvis, próxima.", wake="jarvis"), "próxima.")

    def test_wake_exige_fronteira(self):
        self.assertIsNone(comandos.tira_wake("Computadores são caros."))
        self.assertIsNone(comandos.tira_wake("Computador."))
        self.assertEqual(comandos.tira_wake("Computador: pausar."), "pausar.")


class TestMatchLocal(unittest.TestCase):
    def test_variacoes_pausar(self):
        for f in ("pausar música", "pausa a música", "pause", "para a música"):
            self.assertEqual(comandos.match_local(f), "pausar_musica", f)

    def test_sem_acento_casa(self):
        self.assertEqual(comandos.match_local("pausar musica"), "pausar_musica")
        self.assertEqual(comandos.match_local("proxima musica"), "proxima_faixa")

    def test_outros_de_midia(self):
        self.assertEqual(comandos.match_local("continuar"), "continuar_musica")
        self.assertEqual(comandos.match_local("faixa anterior"), "faixa_anterior")
        self.assertEqual(comandos.match_local("aumentar o volume"), "volume_mais")
        self.assertEqual(comandos.match_local("diminuir volume"), "volume_menos")
        self.assertEqual(comandos.match_local("deixar mudo"), "volume_mudo")

    def test_fora_do_mapa_local_e_none(self):
        self.assertIsNone(comandos.match_local("abrir o banco"))
        self.assertIsNone(comandos.match_local("que dia é hoje"))


class TestExecutar(unittest.TestCase):
    def test_midia_chama_keyboard(self):
        chamadas = []
        with mock.patch.object(comandos, "_teclado", lambda k: chamadas.append(k)):
            msg = comandos.executar("pausar_musica")
        self.assertEqual(chamadas, ["play/pause media"])
        self.assertIn("paus", msg.lower())

    def test_abrir_youtube(self):
        abertas = []
        with mock.patch.object(comandos, "_abrir_url", lambda u: abertas.append(u)):
            comandos.executar("abrir_youtube")
        self.assertEqual(abertas, ["https://www.youtube.com"])

    def test_comando_invalido_levanta(self):
        with self.assertRaises(ValueError):
            comandos.executar("abrir_banco")


class TestDecidir(unittest.TestCase):
    def _resp(self, answers, cost=0.00001, status=200):
        r = mock.Mock(status_code=status)
        r.json.return_value = {"answers": answers, "usage": {"cost": cost}}
        return r

    def test_comando_com_confianca(self):
        ans = {"intencao": {"type": "choice", "choice": "abrir_youtube",
                            "confidence": 0.9}}
        with mock.patch("requests.post", return_value=self._resp(ans)) as p:
            out = comandos.decidir("abrir o youtube", "k")
        self.assertEqual(out, ("abrir_youtube", 0.9, 0.00001))
        corpo = p.call_args.kwargs["json"]
        self.assertEqual(corpo["model"], "typesafe/jev-1.13")
        self.assertIn("ditado", corpo["questions"]["intencao"]["criteria"])
        self.assertEqual(p.call_args.kwargs["timeout"], 8)

    def test_rede_fora_devolve_none(self):
        with mock.patch("requests.post", side_effect=requests.RequestException("dns")):
            self.assertIsNone(comandos.decidir("x", "k"))

    def test_401_devolve_none(self):
        r = mock.Mock(status_code=401)
        r.text = "User not found."
        with mock.patch("requests.post", return_value=r):
            self.assertIsNone(comandos.decidir("x", "k"))

    def test_schema_estranho_devolve_none(self):
        r = mock.Mock(status_code=200)
        r.json.return_value = {"ops": 1}
        with mock.patch("requests.post", return_value=r):
            self.assertIsNone(comandos.decidir("x", "k"))

    def test_opcao_fora_da_lista_devolve_none(self):
        ans = {"intencao": {"type": "choice", "choice": "formatar_pc", "confidence": 1.0}}
        with mock.patch("requests.post", return_value=self._resp(ans)):
            self.assertIsNone(comandos.decidir("x", "k"))

    def test_limiar_padrao_e_env(self):
        self.assertAlmostEqual(comandos.limiar(), 0.6)
        with mock.patch.dict("os.environ", {"COMANDO_CONFIANCA": "0.85"}):
            self.assertAlmostEqual(comandos.limiar(), 0.85)
        with mock.patch.dict("os.environ", {"COMANDO_CONFIANCA": "banana"}):
            self.assertAlmostEqual(comandos.limiar(), 0.6)


class TestTratar(unittest.TestCase):
    def test_local_executa_sem_rede(self):
        with mock.patch("requests.post") as p:
            with mock.patch.object(comandos, "_teclado") as t:
                ok, msg = comandos.tratar("Computador, pausar música.")
        p.assert_not_called()
        t.assert_called_once_with("play/pause media")
        self.assertTrue(ok)
        self.assertIn("paus", msg.lower())

    def test_jev_executa_abrir(self):
        ans = {"intencao": {"type": "choice", "choice": "abrir_youtube", "confidence": 0.9}}
        r = mock.Mock(status_code=200)
        r.json.return_value = {"answers": ans, "usage": {"cost": 0.00001}}
        with mock.patch("requests.post", return_value=r):
            with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}):
                with mock.patch.object(comandos, "_abrir_url") as u:
                    ok, msg = comandos.tratar("Computador, por favor acesse o youtube.")
        self.assertTrue(ok)
        self.assertIn("Youtube", msg)
        self.assertIn("custo", msg)
        u.assert_called_once_with("https://www.youtube.com")

    def test_confianca_baixa_vira_ditado(self):
        ans = {"intencao": {"type": "choice", "choice": "abrir_youtube", "confidence": 0.4}}
        r = mock.Mock(status_code=200)
        r.json.return_value = {"answers": ans, "usage": {"cost": 0.0}}
        with mock.patch("requests.post", return_value=r):
            with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}):
                ok, texto = comandos.tratar("Computador, por favor acesse o youtube.")
        self.assertFalse(ok)
        self.assertNotIn("omputador", texto.lower())

    def test_sem_wake_nem_toca(self):
        with mock.patch("requests.post") as p:
            ok, texto = comandos.tratar("preciso comprar pão.")
        p.assert_not_called()
        self.assertEqual((ok, texto), (False, "preciso comprar pão."))

    def test_sem_key_cai_pra_ditado(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
        with mock.patch.dict("os.environ", env, clear=True):
            with mock.patch("requests.post") as p:
                ok, texto = comandos.tratar("Computador, qual é a capital da França?")
        p.assert_not_called()
        self.assertEqual((ok, texto), (False, "qual é a capital da França?"))

    def test_erro_no_teclado_vira_ditado(self):
        with mock.patch.object(comandos, "_teclado", side_effect=RuntimeError("tecla")):
            ok, texto = comandos.tratar("Computador, pausar música.")
        self.assertEqual((ok, texto), (False, "pausar música."))

    def test_custo_invalido_nao_quebra(self):
        ans = {"intencao": {"type": "choice", "choice": "abrir_youtube", "confidence": 0.9}}
        r = mock.Mock(status_code=200)
        r.json.return_value = {"answers": ans, "usage": {"cost": "gratis"}}
        with mock.patch("requests.post", return_value=r):
            with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}):
                with mock.patch.object(comandos, "_abrir_url"):
                    ok, msg = comandos.tratar("Computador, abre o youtube.")
        self.assertTrue(ok)
        self.assertNotIn("custo", msg)


class TestTocar(unittest.TestCase):
    def test_yt_id_primeira_linha(self):
        with mock.patch.object(comandos, "_yt_run", return_value="abc123\ndef456\n"):
            self.assertEqual(comandos._yt_id("queen"), "abc123")

    def test_yt_id_falha_e_none(self):
        with mock.patch.object(comandos, "_yt_run", side_effect=RuntimeError("rede")):
            self.assertIsNone(comandos._yt_id("queen"))

    def test_tocar_dispara_mpv(self):
        with mock.patch.object(comandos, "_yt_id", return_value="abc123"):
            with mock.patch.object(comandos, "_mpv_ensure", return_value=True) as e:
                with mock.patch.object(comandos, "_mpv_send") as s:
                    msg = comandos.tocar("queen")
        e.assert_called_once_with()
        s.assert_called_once()
        self.assertIn("queen", msg.lower())
        self.assertEqual(s.call_args.args[0], {"command": ["loadfile",
            "https://www.youtube.com/watch?v=abc123", "append-play"]})

    def test_tocar_sem_id_abre_busca(self):
        with mock.patch.object(comandos, "_yt_id", return_value=None):
            with mock.patch.object(comandos, "_abrir_url") as u:
                msg = comandos.tocar("queen")
        u.assert_called_once()
        self.assertIn("search_query=queen", u.call_args.args[0])
        self.assertIn("busca", msg.lower())

    def test_pause_vai_pro_ipc_quando_mpv_vivo(self):
        mpv = mock.Mock()
        mpv.poll.return_value = None
        with mock.patch.object(comandos, "_MPV", mpv):
            with mock.patch.object(comandos, "_mpv_send") as s:
                with mock.patch.object(comandos, "_teclado") as t:
                    comandos.executar("pausar_musica")
        s.assert_called_once_with({"command": ["cycle", "pause"]})
        t.assert_not_called()

    def test_pause_cai_pra_media_key_sem_mpv(self):
        with mock.patch.object(comandos, "_MPV", None):
            with mock.patch.object(comandos, "_mpv_send") as s:
                with mock.patch.object(comandos, "_teclado") as t:
                    comandos.executar("pausar_musica")
        s.assert_not_called()
        t.assert_called_once_with("play/pause media")

    def test_match_local_toca(self):
        self.assertEqual(comandos.match_local("toca bohemian rhapsody"), "tocar_musica")
        self.assertEqual(comandos.match_local("quero ouvir queen"), "tocar_musica")


class TestFalar(unittest.TestCase):
    def test_flag_desligada_nao_sintetiza(self):
        with mock.patch.dict("os.environ", {"COMANDO_VOZ": "0"}):
            with mock.patch.object(comandos, "_sintetizar") as s:
                comandos.falar("oi")
                s.assert_not_called()

    def test_falar_chama_sintese_e_toca(self):
        with mock.patch.dict("os.environ", {"COMANDO_VOZ": "1"}):
            with mock.patch.object(comandos, "_sintetizar", return_value="f.mp3") as s:
                with mock.patch.object(comandos, "_tocar_arquivo") as t:
                    with mock.patch("os.remove") as rm:
                        comandos._falar_sync("São 14h30.")
        s.assert_called_once_with("São 14h30.")
        t.assert_called_once_with("f.mp3")
        rm.assert_called_once()

    def test_sintese_falhando_nao_levanta(self):
        with mock.patch.object(comandos, "_sintetizar", side_effect=RuntimeError("rede")):
            comandos._falar_sync("oi")  # sem exceção = passou


class TestV2Extra(unittest.TestCase):
    def test_abrir_programa(self):
        with mock.patch.object(comandos, "_abrir_app") as a:
            msg = comandos.executar("abrir_programa", "a calculadora")
        a.assert_called_once_with("calc")
        self.assertIn("calculadora", msg.lower())

    def test_abrir_desconhecido_levanta(self):
        with self.assertRaises(ValueError):
            comandos.executar("abrir_programa", "o banco")

    def test_horas(self):
        msg = comandos.executar("dizer_horas")
        self.assertRegex(msg, r"São \d{1,2}h\d{2}")

    def test_pesquisar(self):
        with mock.patch.object(comandos, "_abrir_url") as u:
            comandos.executar("pesquisar_web", "bolo de cenoura")
        self.assertIn("bolo+de+cenoura", u.call_args.args[0])

    def test_match_abrir_local(self):
        self.assertEqual(comandos.match_local("abre o youtube"), "abrir_youtube")
        self.assertEqual(comandos.match_local("abrir a calculadora"), "abrir_programa")
        self.assertEqual(comandos.match_local("que horas são"), "dizer_horas")
        self.assertEqual(comandos.match_local("pesquisa bolo de cenoura"), "pesquisar_web")

    def test_tratar_abrir_fora_do_mapa_vira_ditado(self):
        ok, texto = comandos.tratar("Computador, abre o banco.")
        self.assertEqual((ok, texto), (False, "abre o banco."))


if __name__ == "__main__":
    unittest.main()
