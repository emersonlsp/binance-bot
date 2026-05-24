# Binance API

## Objetivo

Guia prático para:
- conectar na Binance API
- puxar dados de mercado
- enviar ordens

## Tipos de endpoint

- `Public`: não exige assinatura (ex.: book, trades, klines).
- `SIGNED`: exige `timestamp`, `recvWindow` e `signature` HMAC SHA256.

## Credenciais

No `.env`:

```text
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
RECV_WINDOW_MS=5000
```

## Base URLs úteis

- Spot REST: `https://api.binance.com`
- WebSocket Spot: `wss://stream.binance.com:9443/ws`

## Dados de mercado (Spot)

Principais rotas públicas:

- Livro (snapshot): `GET /api/v3/depth`
- Trades recentes: `GET /api/v3/trades`
- Klines: `GET /api/v3/klines`
- Book ticker: `GET /api/v3/ticker/bookTicker`

Principais streams:

- Diff depth stream
- Trade stream
- Book ticker stream

## Assinatura (SIGNED)

Passos:

1. Montar query com `timestamp` e `recvWindow`.
2. Assinar query string com `BINANCE_API_SECRET` em HMAC SHA256.
3. Enviar `signature` + header `X-MBX-APIKEY`.

## Fluxo de ordens (Spot)

1. Validar símbolo e filtros (`LOT_SIZE`, `MIN_NOTIONAL`, `PRICE_FILTER`).
2. Montar ordem (`LIMIT` ou `MARKET`).
3. Enviar ordem assinada.
4. Confirmar status (`NEW`, `PARTIALLY_FILLED`, `FILLED`, `CANCELED`, `REJECTED`, `EXPIRED`).

## Endpoints de ordem (Spot)

- Criar ordem: `POST /api/v3/order` (SIGNED)
- Consultar ordem: `GET /api/v3/order` (SIGNED)
- Cancelar ordem: `DELETE /api/v3/order` (SIGNED)
- Open orders: `GET /api/v3/openOrders` (SIGNED)

## Boas práticas

- Sincronizar relógio da máquina (NTP).
- Tratar retries e rate limits.
- Persistir request/response de ordens para auditoria.
- Nunca commitar credenciais.
- Em produção, usar chave com permissões mínimas.

