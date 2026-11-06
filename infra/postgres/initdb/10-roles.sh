#!/bin/sh
# Runs once, on first initialization of an empty data directory (postgres entrypoint).
# Creates the non-superuser owner role (migrations) and the least-privilege app role.
set -eu
owner_pw="$(cat /run/secrets/db_owner_password)"
app_pw="$(cat /run/secrets/db_app_password)"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$owner_pw" -v app_pw="$app_pw" -v db="${ERP_DB_NAME:-erp}" <<'SQL'
CREATE ROLE erp_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'owner_pw';
CREATE ROLE erp_app   LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_pw';
CREATE DATABASE :"db" OWNER erp_owner;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO erp_app;
\c :"db"
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
