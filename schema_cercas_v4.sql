--
-- PostgreSQL database dump
--

\restrict 1diJ4rCaQtFpJVo2Mv7G4ASnXef8vVtsCNSChxUnh9jqZcb8cht4qGpciWWpFmW

-- Dumped from database version 17.10
-- Dumped by pg_dump version 17.10

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: cercas_central; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cercas_central (
    id integer NOT NULL,
    codigo text NOT NULL,
    tipo text NOT NULL,
    rodovia text NOT NULL,
    cidade text NOT NULL,
    uf text NOT NULL,
    velocidade integer NOT NULL,
    seq integer NOT NULL,
    extensao_m real,
    vertice_inicial text,
    vertice_final text,
    num_vertices integer,
    data_criacao text,
    execucao_id text,
    status text DEFAULT 'ativo'::text NOT NULL,
    superado_em text,
    superado_motivo text
);


--
-- Name: cercas_central_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cercas_central_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cercas_central_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cercas_central_id_seq OWNED BY public.cercas_central.id;


--
-- Name: cercas_central id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cercas_central ALTER COLUMN id SET DEFAULT nextval('public.cercas_central_id_seq'::regclass);


--
-- Name: cercas_central cercas_central_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cercas_central
    ADD CONSTRAINT cercas_central_pkey PRIMARY KEY (id);


--
-- PostgreSQL database dump complete
--

\unrestrict 1diJ4rCaQtFpJVo2Mv7G4ASnXef8vVtsCNSChxUnh9jqZcb8cht4qGpciWWpFmW

