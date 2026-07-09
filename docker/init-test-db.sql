-- Runs once on first container start (docker-entrypoint-initdb.d). Creates
-- the second database used by the test suite, alongside the threadbare_dev
-- database Postgres creates from POSTGRES_DB, so tests never touch dev data.
CREATE DATABASE threadbare_test OWNER threadbare;
