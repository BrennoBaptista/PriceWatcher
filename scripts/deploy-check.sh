#!/usr/bin/env bash
# Diagnostico de deploy do PriceWatcher.
#
# Roda no servidor, na raiz do repositorio. Nao altera nada alem de construir a
# imagem: nao sobe servico, nao apaga dado, nao envia mensagem.
#
# Ordem proposital: a compatibilidade de CPU e checada ANTES do build, porque e
# a pergunta que decide o resto (secao 11.2 da SPEC). Se o processador nao tiver
# as instrucoes que o libcurl-impersonate espera, nao adianta construir nada.
#
#   bash scripts/deploy-check.sh
#
# Gera um .log com tudo. NENHUM segredo e impresso -- o .env so e verificado
# quanto a presenca das chaves, nunca quanto ao valor.

set -uo pipefail

LOG="deploy-check-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee "$LOG") 2>&1

titulo() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }
ok()     { printf '  [OK]    %s\n' "$1"; }
aviso()  { printf '  [AVISO] %s\n' "$1"; }
erro()   { printf '  [ERRO]  %s\n' "$1"; }

FALHAS=0

titulo "1. Sistema"
. /etc/os-release 2>/dev/null && echo "  distro : ${PRETTY_NAME:-desconhecida}"
echo "  kernel : $(uname -sr)"
echo "  arq    : $(uname -m)"
echo "  nucleos: $(nproc 2>/dev/null || echo '?')"
free -h 2>/dev/null | awk '/^Mem:/ {print "  memoria: " $2 " total, " $7 " disponivel"}'

titulo "2. CPU -- a pergunta que decide o deploy"
CPU=$(awk -F': ' '/model name/ {print $2; exit}' /proc/cpuinfo)
echo "  modelo : ${CPU:-desconhecido}"
FLAGS=$(awk '/^flags/ {print; exit}' /proc/cpuinfo)
# Atencao ao nome: o kernel chama SSE3 de 'pni' (Prescott New Instructions).
# Procurar por 'sse3' da falso negativo em CPU que tem SSE3.
for par in "SSE3:pni" "SSSE3:ssse3" "SSE4.1:sse4_1" "SSE4.2:sse4_2" \
           "POPCNT:popcnt" "AVX:avx" "AVX2:avx2"; do
    rotulo=${par%%:*}; flag=${par##*:}
    if grep -qw "$flag" <<<"$FLAGS"; then ok "$rotulo presente"
    else aviso "$rotulo ausente"; fi
done
if grep -qw sse4_2 <<<"$FLAGS" && grep -qw popcnt <<<"$FLAGS"; then
    echo
    ok "CPU atende x86-64-v2. Binarios modernos devem rodar."
else
    echo
    aviso "CPU e x86-64 baseline (v1). O binario do curl_cffi PODE nao rodar."
    aviso "O passo 6 (--selftest) e quem da a resposta definitiva."
fi

titulo "3. Docker"
if command -v docker >/dev/null 2>&1; then
    ok "docker: $(docker --version)"
    if docker compose version >/dev/null 2>&1; then
        ok "compose: $(docker compose version --short 2>/dev/null)"
    else
        erro "'docker compose' (v2) ausente -- instale o plugin docker-compose-plugin"
        FALHAS=$((FALHAS+1))
    fi
    if docker info >/dev/null 2>&1; then
        ok "daemon respondendo, usuario tem permissao"
    else
        erro "daemon nao responde para este usuario"
        aviso "provavel: faltou 'sudo usermod -aG docker \$USER' e relogar"
        FALHAS=$((FALHAS+1))
    fi
else
    erro "docker nao instalado"
    FALHAS=$((FALHAS+1))
fi

titulo "4. Configuracao (valores nunca sao impressos)"
if [ -f .env ]; then
    ok ".env presente"
    for chave in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID TELEGRAM_GROUP_CHAT_ID; do
        valor=$(grep -E "^${chave}=" .env | cut -d= -f2- | tr -d '\r' | xargs 2>/dev/null)
        if [ -n "$valor" ]; then ok "$chave definido (${#valor} caracteres)"
        else erro "$chave vazio ou ausente"; FALHAS=$((FALHAS+1)); fi
    done
else
    erro ".env ausente -- rode: cp .env.example .env  e preencha"
    FALHAS=$((FALHAS+1))
fi
[ -f config.yaml ] && ok "config.yaml presente" || { erro "config.yaml ausente"; FALHAS=$((FALHAS+1)); }

if [ "$FALHAS" -gt 0 ]; then
    titulo "Interrompido"
    erro "$FALHAS problema(s) antes do build. Resolva e rode de novo."
    echo "  log: $LOG"
    exit 1
fi

titulo "5. Build da imagem (primeira vez que este Dockerfile e construido)"
if docker compose build; then
    ok "imagem construida"
else
    erro "build falhou -- ver saida acima"
    echo "  log: $LOG"
    exit 1
fi

titulo "6. Autoteste -- o curl_cffi roda neste CPU? As lojas respondem?"
if docker compose run --rm pricewatcher --selftest; then
    ok "ambiente apto"
else
    erro "autoteste falhou"
    aviso "Se falhou ao CARREGAR o curl_cffi, e o risco da secao 11.2 se"
    aviso "materializando: o plano B (curl do sistema) precisa entrar."
    aviso "Se carregou mas as lojas deram erro, e bloqueio ou rede."
    echo "  log: $LOG"
    exit 1
fi

titulo "Tudo certo"
cat <<'FIM'
  Proximos passos, um de cada vez:

    docker compose run --rm pricewatcher --test-notify   # canais do Telegram
    docker compose run --rm pricewatcher --run-once      # coleta de verdade
    docker compose up -d                                 # agendador no ar

  Depois: docker compose logs -f
FIM
echo "  log: $LOG"
