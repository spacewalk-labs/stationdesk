# Build in a disposable builder without drawings or secrets.
FROM rust@sha256:81015672d6939b0265f09adf96d5de125482bb1330106076956b0518e74edc90
ARG CADCRAFT_COMMIT=e707ab4d7cc10102e550ad4c5bcecef1084a9baa
ARG CRAFT_FONTS_COMMIT=3d9ab3a132ed0eff457e424226a435a901da30d3
RUN apt-get update -qq && apt-get install -y --no-install-recommends zip patch fonts-nanum \
    && rm -rf /var/lib/apt/lists/*
RUN rustup target add wasm32-unknown-unknown \
    && curl -fLsS https://github.com/trunk-rs/trunk/releases/download/v0.21.14/trunk-x86_64-unknown-linux-gnu.tar.gz \
       | tar xz -C /usr/local/bin trunk
WORKDIR /build
RUN curl -fLsS "https://github.com/storytold/cadcraft/archive/$CADCRAFT_COMMIT.tar.gz" \
      | tar xz --strip-components=1 \
    && mkdir /fonts \
    && curl -fLsS "https://github.com/storytold/craft-fonts/archive/$CRAFT_FONTS_COMMIT.tar.gz" \
      | tar xz -C /fonts --strip-components=1
RUN cp /usr/share/fonts/truetype/nanum/NanumGothic.ttf /fonts/fonts/NanumGothic.ttf
COPY nanum-gothic.patch /tmp/nanum-gothic.patch
RUN patch -p1 < /tmp/nanum-gothic.patch \
    && printf '\nNanumGothic | Regular | fonts/NanumGothic.ttf | Hang\n' >> /fonts/fonts/manifest.txt
COPY host-run.patch /tmp/host-run.patch
RUN patch -p1 < /tmp/host-run.patch
ENV CRAFT_FONTS_DIR=/fonts CRAFT_FONTS_REQUIRED=1 CARGO_BUILD_JOBS=2
RUN packaging/web/package.sh
# The embedded Nanum font must travel with its OFL notice.
RUN cp /usr/share/doc/fonts-nanum/copyright /fonts/NANUM-LICENSE.txt
RUN cp /fonts/NANUM-LICENSE.txt dist/web/ && packaging/web/package.sh --skip-build
