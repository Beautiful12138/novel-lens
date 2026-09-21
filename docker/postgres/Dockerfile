# 在现有 PostgreSQL 18.6 基础上增加全文扩展，保留官方入口和数据目录。
FROM mirror.ccs.tencentyun.com/library/postgres:18@sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280

ARG PGROONGA_VERSION=4.0.8-1

# 官方仓库引导包固定校验和；上游 latest 变化时构建失败，须核实后更新。
ADD https://packages.groonga.org/debian/groonga-apt-source-latest-trixie.deb \
    /tmp/groonga-apt-source.deb

RUN set -eux; \
    echo '3406de4b8965c44a0e793090efbb0996a1802930159e7aa3f31c97dbef127c34  /tmp/groonga-apt-source.deb' | sha256sum -c -; \
    apt-mark hold postgresql-18 postgresql-client-18; \
    apt-get update; \
    apt-get install -y --no-install-recommends /tmp/groonga-apt-source.deb; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      postgresql-18-pgdg-pgroonga="${PGROONGA_VERSION}"; \
    test "$(postgres --version)" = 'postgres (PostgreSQL) 18.6 (Debian 18.6-1.pgdg13+2)'; \
    test "$(dpkg-query -W -f='${Version}' postgresql-18-pgdg-pgroonga)" = "${PGROONGA_VERSION}"; \
    rm -f /tmp/groonga-apt-source.deb; \
    rm -rf /var/lib/apt/lists/*
