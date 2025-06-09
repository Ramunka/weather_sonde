--
-- PostgreSQL database dump
--

-- Dumped from database version 15.13 (Debian 15.13-0+deb12u1)
-- Dumped by pg_dump version 15.13 (Debian 15.13-0+deb12u1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: raw; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA raw;


ALTER SCHEMA raw OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: packets; Type: TABLE; Schema: raw; Owner: postgres
--

CREATE TABLE raw.packets (
    id bigint NOT NULL,
    recv_ts timestamp without time zone DEFAULT now() NOT NULL,
    payload text NOT NULL,
    rssi_dbm real,
    snr_db real
);


ALTER TABLE raw.packets OWNER TO postgres;

--
-- Name: packets_id_seq; Type: SEQUENCE; Schema: raw; Owner: postgres
--

CREATE SEQUENCE raw.packets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE raw.packets_id_seq OWNER TO postgres;

--
-- Name: packets_id_seq; Type: SEQUENCE OWNED BY; Schema: raw; Owner: postgres
--

ALTER SEQUENCE raw.packets_id_seq OWNED BY raw.packets.id;


--
-- Name: packets id; Type: DEFAULT; Schema: raw; Owner: postgres
--

ALTER TABLE ONLY raw.packets ALTER COLUMN id SET DEFAULT nextval('raw.packets_id_seq'::regclass);


--
-- Data for Name: packets; Type: TABLE DATA; Schema: raw; Owner: postgres
--

COPY raw.packets (id, recv_ts, payload, rssi_dbm, snr_db) FROM stdin;
\.


--
-- Name: packets_id_seq; Type: SEQUENCE SET; Schema: raw; Owner: postgres
--

SELECT pg_catalog.setval('raw.packets_id_seq', 1, false);


--
-- Name: packets packets_pkey; Type: CONSTRAINT; Schema: raw; Owner: postgres
--

ALTER TABLE ONLY raw.packets
    ADD CONSTRAINT packets_pkey PRIMARY KEY (id);


--
-- Name: idx_raw_packets_recv_ts; Type: INDEX; Schema: raw; Owner: postgres
--

CREATE INDEX idx_raw_packets_recv_ts ON raw.packets USING btree (recv_ts);


--
-- Name: SCHEMA raw; Type: ACL; Schema: -; Owner: postgres
--

GRANT USAGE ON SCHEMA raw TO ingest_user;


--
-- Name: TABLE packets; Type: ACL; Schema: raw; Owner: postgres
--

GRANT SELECT,INSERT ON TABLE raw.packets TO ingest_user;


--
-- PostgreSQL database dump complete
--

