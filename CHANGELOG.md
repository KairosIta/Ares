# Changelog

Le modifiche rilevanti di Ares sono raccolte in questo file. Il formato segue
[Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/) e il progetto
adotta il versionamento semantico a partire dal primo rilascio pubblico.

## [Unreleased]

### Added

- interfaccia Rich con streaming Markdown, pannelli e output sicuro per pipe;
- editor Prompt Toolkit con menu dei comandi, suggerimenti, input multilinea
  e cronologia privata coordinata fra sessioni concorrenti;
- backend cooperativo dei lock condivisi/esclusivi per POSIX e Windows, con
  lock delle dipendenze universale e matrice CI sui due sistemi;
- setup PowerShell idempotente e documentazione d'installazione per Windows;
- pubblicazione e restore transazionali dei backup sui filesystem Windows.

### Security

- sequenze di controllo del terminale filtrate dalle risposte del modello;
- cronologia della chat atomica, limitata e creata con permessi `0600`.

## [0.1.0] - 2026-08-25

### Added

- assistente locale basato su Agno e Ollama;
- cinque store di memoria persistente e quaderno privato;
- strumenti per cronologia, sessioni, entità e conoscenza riutilizzabile;
- workspace controllato con conferme per le operazioni sensibili;
- snapshot locali verificati con restore e retention;
- audit e fusione reversibile delle entità duplicate;
- suite isolata e prove E2E contro il modello locale.
- documentazione pubblica, policy di sicurezza e guida ai contributi;
- CI senza GPU e aggiornamenti automatici delle dipendenze.

### Security

- telemetria Agno disabilitata;
- namespace isolati e lock cooperativo dello stato;
- dati persistenti, snapshot e configurazione locale esclusi dal repository.

[Unreleased]: https://github.com/KairosIta/Ares/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/KairosIta/Ares/releases/tag/v0.1.0
