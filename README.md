# audio-md

Transcreve um arquivo de áudio ou um **vídeo do YouTube** localmente e destila o
conteúdo em um resumo Markdown. A pasta de saída é um trecho curto do **SHA-256**
do arquivo (ou o **id do vídeo** no YouTube), então a mesma entrada nunca é
reprocessada.

![Interface web — arraste os áudios, ajuste a ordem e envie](docs/screenshots/web-home.png)

![Resultado — transcrição e resumo lado a lado](docs/screenshots/web-result.png)

```
arquivo de áudio ->  ./outputs/audios/{hash-curto}/transcript.txt
                     ./outputs/audios/{hash-curto}/summary.md
                     ./outputs/audios/{hash-curto}/meta.json   # inclui o sha256 completo

URL do YouTube   ->  ./outputs/youtube/{video-id}/transcript.txt
                     ./outputs/youtube/{video-id}/summary.md
                     ./outputs/youtube/{video-id}/meta.json    # inclui título/canal/url
```

- **Download do YouTube:** [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) — só o
  stream de áudio, em um diretório temporário apagado após a transcrição.
- **Transcrição:** [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper)
  (GPU, com fallback automático para CPU).
- **Resumo:** um agent CLI headless — `claude`, `opencode` ou `codex` — sem API
  key para gerenciar (cada um usa o próprio login).

---

## Como funciona

1. **Identifica** a entrada: arquivo de áudio é hasheado (SHA-256; os 8 primeiros
   caracteres nomeiam a pasta de saída); URL/id do YouTube usa o id do vídeo como
   nome da pasta. Nos dois casos, transcrição em cache pula direto para o resumo —
   áudio do YouTube nem chega a ser baixado de novo.
2. **Transcreve** com faster-whisper (GPU, caindo para CPU automaticamente), com
   barra de progresso sobre a linha do tempo do áudio.
3. **Resume** a transcrição em `summary.md` com o agent CLI escolhido
   (`claude`/`opencode`/`codex`).

Cada etapa imprime um cabeçalho `▸`, então dá para saber sempre onde a execução está.

---

## Instalação

Requer **Python ≥ 3.12**, [uv](https://docs.astral.sh/uv/), `ffmpeg` e — para o
resumo — um agent CLI logado: o [`claude` CLI](https://claude.com/claude-code)
(`claude login`), `opencode` ou `codex`.

```bash
uv sync --extra transcribe                 # só CPU: faster-whisper + CTranslate2
uv sync --extra transcribe --extra gpu     # + aceleração GPU (NVIDIA, recomendado)
```

(ou `make deps` — GPU por padrão; `make deps EXTRAS="--extra transcribe"` para só CPU)

`--extra transcribe` traz faster-whisper + CTranslate2. Sem ele o pacote ainda
roda, mas a transcrição falha até que seja instalado.

Para instalar como comando standalone (e largar o prefixo `uv run`):

```bash
uv tool install '.[transcribe,gpu]'
audio-md --help
```

### GPU (recomendado na RTX 4050)

O extra `gpu` embute as bibliotecas CUDA de que o CTranslate2 precisa (cuDNN 9 +
cuBLAS 12). Elas são **pré-carregadas automaticamente** em runtime, então não há
`LD_LIBRARY_PATH` para configurar — basta instalar o extra e a GPU é usada.

O device é escolhido por `WHISPER_DEVICE` (ou `--device`):

- `auto` (padrão) — tenta a GPU e cai para a CPU se falhar.
- `cuda` — força a GPU (dá erro se não conseguir, em vez de usar a CPU em silêncio).
- `cpu` — força a CPU.

O `meta.json` registra o `device` realmente usado, para conferir se a GPU rodou.
O `large-v3` com `int8_float16` usa ~3 GB de VRAM (cabe em 6 GB). Em máquinas mais
fracas use `--model medium` ou `--model small`, ou troque precisão por velocidade
com `--beam-size 1`.

---

## Uso (CLI)

```bash
uv run audio-md caminho/do/audio.mp3
uv run audio-md "https://www.youtube.com/watch?v=awdC4RZdT8A"   # URL do YouTube
uv run audio-md awdC4RZdT8A                                     # ou só o id do vídeo
uv run audio-md audio.wav --model medium --lang pt
uv run audio-md audio.ogg --no-summary      # só a transcrição
uv run audio-md audio.mp3 --force           # ignora o cache (YouTube: baixa de novo também)

# escolhendo o CLI de resumo:
uv run audio-md audio.mp3 --provider claude-cli --summary-model opus
uv run audio-md audio.mp3 --provider codex-cli  --summary-model gpt-5.5
uv run audio-md audio.mp3 --provider opencode   --summary-model anthropic/claude-sonnet-4-5
```

Opções: `--model` (whisper), `--device` (auto/cuda/cpu), `--beam-size`,
`--batch-size`, `--lang` (vazio = auto-detecta), `--provider`
(claude-cli/opencode/codex-cli), `--summary-model`, `--no-summary`, `--force`,
`--outdir`. Variáveis de ambiente equivalentes no `.env` (veja `.env.example`).

Também roda como módulo: `uv run python -m audio_md audio.mp3`.

---

## Interface web

```bash
make run          # ou: uv run audio-md-web → http://127.0.0.1:8765
```

Arraste vários arquivos de áudio como **um grupo só** (pense nos áudios picotados
do WhatsApp sobre o mesmo assunto), ajuste a ordem e receba uma transcrição
juntada + um único resumo, lado a lado. Os grupos ficam em
`outputs/groups/{hash-do-grupo}/` e servem de histórico do site; as transcrições
por arquivo compartilham o cache do CLI em `outputs/audios/`, nos dois sentidos.
A configuração vem do `.env` (a UI não tem opções).

### Rodando como serviço (systemd)

Mantém o `audio-md-web` sempre no ar como serviço **user-level** do systemd:
instala e gerencia sem sudo, roda com o seu usuário (GPU e login do `claude`
funcionam normalmente) e reinicia sozinho se cair.

```bash
make install
```

Isso roda `uv sync` com os extras de GPU, renderiza `audio-md.service` com o
caminho do repositório para `~/.config/systemd/user/` e ativa o serviço.

Para o serviço subir no boot **sem precisar de login** (o `make install` tenta,
mas normalmente exige sudo):

```bash
sudo loginctl enable-linger $USER
```

### Domínio local (audio-md.local)

A porta padrão é **8765** — para mudar, defina `WEB_PORT=` no `.env` e rode
`make restart`. Aponte o domínio para o loopback:

```bash
echo "127.0.0.1 audio-md.local" | sudo tee -a /etc/hosts
```

→ **http://audio-md.local:8765**

`/etc/hosts` só resolve nome → IP, não mapeia porta. Para acessar **sem** a
porta (**http://audio-md.local**), use o proxy nginx incluído no repositório
(`audio-md.nginx.conf` — repassa a porta 80 para a 8765, com
`client_max_body_size 1g` para os uploads não esbarrarem no limite do nginx):

```bash
sudo cp audio-md.nginx.conf /etc/nginx/sites-available/audio-md.local
sudo ln -sf ../sites-available/audio-md.local /etc/nginx/sites-enabled/audio-md.local
sudo nginx -t && sudo systemctl reload nginx
```

### Operação

| comando                           | faz                                   |
| --------------------------------- | ------------------------------------- |
| `make status`                     | estado do serviço                     |
| `make logs`                       | segue os logs (journalctl)            |
| `make start` / `stop` / `restart` | controla o serviço                    |
| `make run`                        | roda em foreground (desenvolvimento)  |
| `make uninstall`                  | para, desativa e remove o serviço     |

Notas:

- O resumo usa o agent CLI configurado no `.env` (`claude` por padrão); ele
  precisa estar **logado no mesmo usuário** que roda o serviço. O unit já põe
  `~/.local/bin` e `~/.opencode/bin` no PATH.
- O servidor escuta só em `127.0.0.1` — nada é exposto para a rede.
- Depois de editar o `.env` (modelo whisper, device, provider, porta),
  `make restart`.

---

## Layout

```
src/audio_md/
  cli.py          # argparse + entry point (arquivo vs YouTube)
  config.py       # Settings (args + .env)
  console.py      # saída rich — único escritor no terminal (etapas + barra de progresso)
  hashing.py      # sha256 em streaming
  youtube.py      # parsing de video-id + download só de áudio (yt-dlp)
  transcribe.py   # faster-whisper (GPU/CPU)
  providers.py    # agent CLIs de resumo (claude/opencode/codex, headless)
  summarize.py    # prompt + despacho de provider
  pipeline.py     # hash/download → transcrição → resumo
  web.py          # UI web local (Flask): grupos ordenados → transcrição juntada + um resumo
  static/         # frontend da dropzone (sem build; marked + DOMPurify vendorizados)
```

## Notas

- Formatos: qualquer coisa que o `ffmpeg` decodifique (mp3, wav, ogg, m4a, ...).
- O `meta.json` registra o modelo whisper, o idioma detectado, a duração, o tempo
  de transcrição e o provider/modelo usado no resumo.
