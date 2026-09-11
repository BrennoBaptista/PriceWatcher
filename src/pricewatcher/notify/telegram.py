"""Transporte: Bot API do Telegram.

Duas coisas aprendidas na pratica e codificadas aqui:

* **UTF-8 explicito.** O primeiro envio de teste morreu com
  `Bad Request: strings must be encoded in UTF-8` porque o corpo foi montado com
  o encoding do ambiente. Toda mensagem nossa tem "mínimo" e "R$", entao isso
  quebraria em producao. O corpo vai como JSON serializado em UTF-8, sempre.
* **`migrate_to_chat_id`.** Se um grupo comum virar supergrupo, o id antigo para
  de funcionar e a API devolve o novo. Logamos isso de forma bem visivel em vez
  de so falhar -- ver secao 8 da SPEC.
"""

from __future__ import annotations

import json
import logging

from curl_cffi import requests as curl_requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{metodo}"
LIMITE_TELEGRAM = 4096


class TelegramNotifier:
    def __init__(self, token: str, timeout: int = 20) -> None:
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN vazio")
        self._token = token
        self._timeout = timeout

    def envia(self, chat_id: str, texto: str) -> bool:
        """True se o Telegram aceitou. Nunca levanta -- o chamador isola destinos."""
        for pedaco in _fatia(texto, LIMITE_TELEGRAM):
            if not self._envia_um(chat_id, pedaco):
                return False
        return True

    def _envia_um(self, chat_id: str, texto: str) -> bool:
        corpo = json.dumps(
            {
                "chat_id": chat_id,
                "text": texto,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")  # <- explicito, nao herdado do ambiente

        try:
            r = curl_requests.post(
                API.format(token=self._token, metodo="sendMessage"),
                data=corpo,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=self._timeout,
            )
        except Exception as e:  # noqa: BLE001 -- falha de rede nao pode derrubar a rodada
            log.error("telegram: falha de rede ao enviar para %s: %s", chat_id, e)
            return False

        try:
            resposta = r.json()
        except ValueError:
            log.error("telegram: resposta nao-JSON (%s): %s", r.status_code, r.text[:200])
            return False

        if resposta.get("ok"):
            return True

        novo = (resposta.get("parameters") or {}).get("migrate_to_chat_id")
        if novo:
            log.error(
                "TELEGRAM: o grupo %s virou supergrupo. ATUALIZE o .env: "
                "TELEGRAM_GROUP_CHAT_ID=%s",
                chat_id, novo,
            )
        else:
            log.error(
                "telegram: envio para %s recusado (%s): %s",
                chat_id, resposta.get("error_code"), resposta.get("description"),
            )
        return False


def _fatia(texto: str, limite: int) -> list[str]:
    """Quebra em blocos, preferindo separar entre alertas."""
    if len(texto) <= limite:
        return [texto]
    pedacos, atual = [], ""
    for bloco in texto.split("\n\n"):
        candidato = f"{atual}\n\n{bloco}" if atual else bloco
        if len(candidato) > limite and atual:
            pedacos.append(atual)
            atual = bloco
        else:
            atual = candidato
    if atual:
        pedacos.append(atual)
    return pedacos
