# The deterministic PDF builds need the TeX Live 2023 that produced the
# published resume (pdfTeX-1.40.25), so it is installed from the frozen historic
# repository, pinned. scheme-infraonly, no docs or sources, then exactly the
# packages the template loads — derived from artifacts/career/Alex_Example.fls
# (every INPUT file's owning package, mapped against texlive.tlpdb) — plus
# latexmk.
FROM python:3.13-slim AS tex

ARG TEXLIVE_MIRROR=https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2023/tlnet-final
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates perl wget xz-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/texlive
RUN wget -q "$TEXLIVE_MIRROR/install-tl-unx.tar.gz" -O install-tl-unx.tar.gz \
 && tar -xzf install-tl-unx.tar.gz \
 && printf '%s\n' \
      'selected_scheme scheme-infraonly' \
      'TEXDIR /opt/texlive' \
      'TEXMFCONFIG ~/.texlive/texmf-config' \
      'TEXMFHOME ~/texmf' \
      'TEXMFLOCAL /opt/texlive/texmf-local' \
      'TEXMFSYSCONFIG /opt/texlive/texmf-config' \
      'TEXMFSYSVAR /opt/texlive/texmf-var' \
      'TEXMFVAR ~/.texlive/texmf-var' \
      'option_doc 0' \
      'option_src 0' \
      'instopt_adjustpath 0' \
    > texlive.profile \
 && install-tl-*/install-tl --profile texlive.profile --repository "$TEXLIVE_MIRROR" \
 && /opt/texlive/bin/*/tlmgr --repository "$TEXLIVE_MIRROR" install \
      latex latex-bin amsmath atbegshi atveryend auxhook babel babel-english bigintcalc \
      bitset carlisle cm ec enumitem etoolbox fancyhdr fontaxes fontname \
      gettitlestring graphics graphics-cfg graphics-def hycolor hyperref iftex \
      infwarerr intcalc kastrup kvdefinekeys kvoptions kvsetkeys l3backend \
      l3kernel l3packages latex-fonts latexmk ltxcmds marvosym mptopdf mweights \
      newtx oberdiek pdfescape pdftex pdftexcmds preprint refcount rerunfilecheck \
      titlesec tools uniquecounter url xcolor xkeyval xpatch xstring \
 && ln -s /opt/texlive/bin/*/ /opt/texlive/bin/platform

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.23

# perl runs latexmk; poppler-utils provides pdfinfo and pdftotext for the
# page-count and two-rendered-line bullet checks.
RUN apt-get update \
 && apt-get install -y --no-install-recommends perl poppler-utils \
 && rm -rf /var/lib/apt/lists/*

COPY --from=tex /opt/texlive /opt/texlive

# The workspace root owns the lock, so both members must be present for uv to
# resolve it. Only `personal-os-backend` is installed.
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/README.md ./backend/
COPY workers/pyproject.toml workers/README.md ./workers/
COPY backend/src ./backend/src
RUN uv sync --frozen --no-dev --package personal-os-backend

ENV PATH="/app/.venv/bin:/opt/texlive/bin/platform:$PATH"
CMD ["sh", "-c", "uvicorn personal_os_backend.main:app --host 0.0.0.0 --port ${PORT}"]
