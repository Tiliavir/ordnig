# Both stages pinned to the same digest; Dependabot proposes updates for both.

# --- veraPDF and a minimal Java runtime; curl, unzip and the JDK stay here.
FROM ubuntu:25.10@sha256:7cc5e35f6567ee8c66d2abb4aab0fd866669e6207c237c3a8f0947a5c7f17092 AS java

ARG DEBIAN_FRONTEND=noninteractive
# veraPDF publishes only a "latest" installer; CI smoke-tests every build.
ARG VERAPDF_URL=https://software.verapdf.org/releases/verapdf-installer.zip

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip default-jdk-headless \
 && rm -rf /var/lib/apt/lists/*

COPY docker/verapdf-auto-install.xml /tmp/auto-install.xml
RUN curl -fsSL -o /tmp/vera.zip "$VERAPDF_URL" \
 && unzip -q /tmp/vera.zip -d /tmp/vera \
 && sh "$(find /tmp/vera -maxdepth 2 -type f -name '*install' | head -1)" /tmp/auto-install.xml

# Modules as in veraPDF's own Docker image (veraPDF-apps/Dockerfile).
RUN jlink --add-modules java.base,java.compiler,java.logging,java.xml,jdk.crypto.ec,java.desktop,jdk.management \
      --strip-debug --no-man-pages --no-header-files --compress=zip-6 --output /opt/java \
 && JAVA_HOME=/opt/java /opt/verapdf/verapdf --version

# --- Runtime image
FROM ubuntu:25.10@sha256:7cc5e35f6567ee8c66d2abb4aab0fd866669e6207c237c3a8f0947a5c7f17092

ARG DEBIAN_FRONTEND=noninteractive
ARG ORDNIG_VERSION=dev

# Last line: native libraries of the Java runtime (normally pulled in by the JRE package).
RUN apt-get update && apt-get -y upgrade && apt-get install -y --no-install-recommends \
      python3 file tini ca-certificates \
      ocrmypdf ghostscript img2pdf \
      tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng tesseract-ocr-frk \
      tesseract-ocr-fra tesseract-ocr-ita \
      libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
      fonts-dejavu fonts-liberation fonts-liberation2 fonts-noto-core \
      fonts-crosextra-carlito fonts-crosextra-caladea \
      libfreetype6 libgif7 libharfbuzz0b libjpeg8 liblcms2-2 libpng16-16t64 \
 && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/opt/java
COPY --from=java /opt/java /opt/java
COPY --from=java /opt/verapdf /opt/verapdf
# Fail the build if the Java runtime misses a system library (X11 and sound are unused).
RUN missing=$(find /opt/java/lib -name '*.so' ! -name libawt_xawt.so ! -name libjsound.so \
      -exec env LD_LIBRARY_PATH=/opt/java/lib/server ldd {} + | grep 'not found' | sort -u) \
 && { [ -z "$missing" ] || { echo "$missing"; exit 1; }; } \
 && printf '#!/bin/sh\nexec /opt/verapdf/verapdf "$@"\n' > /usr/local/bin/verapdf \
 && chmod +x /usr/local/bin/verapdf \
 && verapdf --version

COPY src/check.py src/migrate.py LICENSE /opt/ordnig/
COPY src/ordnig /usr/local/bin/ordnig
RUN chmod +x /usr/local/bin/ordnig \
 && { echo "ordnig:      $ORDNIG_VERSION"; \
      echo "veraPDF:     $(verapdf --version | head -1)"; \
      echo "Java:        $(/opt/java/bin/java -version 2>&1 | head -1)"; \
      echo "OCRmyPDF:    $(ocrmypdf --version)"; \
      echo "Tesseract:   $(tesseract --version 2>&1 | head -1)"; \
      echo "LibreOffice: $(soffice --version | head -1)"; \
      echo "Ghostscript: $(gs --version)"; \
      echo "Python:      $(python3 --version)"; \
      echo "Base:        $(. /etc/os-release && echo "$PRETTY_NAME")"; } > /opt/ordnig/versions.txt

# Runs fine as any UID (docker run --user); LibreOffice gets a temp profile per job.
ENV HOME=/tmp OMP_THREAD_LIMIT=1 PYTHONDONTWRITEBYTECODE=1
VOLUME ["/ablage", "/report"]
ENTRYPOINT ["tini", "--", "ordnig"]
CMD ["preview"]
