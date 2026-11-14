FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
# veraPDF publishes only a "latest" installer; CI smoke-tests every build.
ARG VERAPDF_URL=https://software.verapdf.org/releases/verapdf-installer.zip

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 file tini ca-certificates curl unzip \
      ocrmypdf ghostscript img2pdf pngquant unpaper \
      tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng tesseract-ocr-frk \
      tesseract-ocr-fra tesseract-ocr-ita \
      libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
      fonts-dejavu fonts-liberation fonts-liberation2 fonts-noto-core \
      fonts-crosextra-carlito fonts-crosextra-caladea \
      default-jre-headless \
 && rm -rf /var/lib/apt/lists/*

COPY docker/verapdf-auto-install.xml /tmp/auto-install.xml
RUN curl -fsSL -o /tmp/vera.zip "$VERAPDF_URL" \
 && unzip -q /tmp/vera.zip -d /tmp/vera \
 && sh "$(find /tmp/vera -maxdepth 2 -type f -name '*install' | head -1)" /tmp/auto-install.xml \
 && printf '#!/bin/sh\nexec /opt/verapdf/verapdf "$@"\n' > /usr/local/bin/verapdf \
 && chmod +x /usr/local/bin/verapdf \
 && verapdf --version \
 && rm -rf /tmp/vera /tmp/vera.zip /tmp/auto-install.xml

COPY src/check.py src/migrate.py /opt/ordnig/
COPY src/ordnig /usr/local/bin/ordnig
RUN chmod +x /usr/local/bin/ordnig

# Runs fine as any UID (docker run --user); LibreOffice gets a temp profile per job.
ENV HOME=/tmp OMP_THREAD_LIMIT=1 PYTHONDONTWRITEBYTECODE=1
VOLUME ["/ablage", "/report"]
ENTRYPOINT ["tini", "--", "ordnig"]
CMD ["preview"]
