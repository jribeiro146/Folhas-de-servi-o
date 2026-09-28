"use strict";
// Preload dos testes: nenhum transporte real é necessário neste pacote.
const blocked = () => { throw new Error("Rede bloqueada nos testes de migração; utilizar mocks."); };
globalThis.fetch = blocked;
globalThis.WebSocket = blocked;
for (const [name, methods] of Object.entries({
    http: ["request", "get"], https: ["request", "get"],
    net: ["connect", "createConnection"], tls: ["connect"],
    dgram: ["createSocket"], dns: ["lookup", "resolve"],
})) {
    const module = require(`node:${name}`);
    methods.forEach(method => { module[method] = blocked; });
}
require("node:net").Socket.prototype.connect = blocked;
