"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");
const root = path.resolve(__dirname, "..");
const walk = dir => fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const target = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`Ligação simbólica inesperada: ${target}`);
    return entry.isDirectory() ? walk(target) : [target];
});
const relative = file => path.relative(root, file).split(path.sep).join("/");
const files = walk(root);
const manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest-sha256.json"), "utf8"));
const failures = [];
const expected = new Set(manifest.files.map(item => item.path));
for (const item of manifest.files) {
    const target = path.resolve(root, ...item.path.split("/"));
    if (!target.startsWith(root + path.sep)) throw new Error("Caminho fora do pacote no manifesto.");
    if (!fs.existsSync(target)) { failures.push(`${item.path}: ausente`); continue; }
    const hash = crypto.createHash("sha256").update(fs.readFileSync(target)).digest("hex");
    if (hash !== item.sha256) failures.push(`${item.path}: conteúdo alterado`);
}
for (const file of files) {
    const rel = relative(file);
    if (rel !== "manifest-sha256.json" && !expected.has(rel)) failures.push(`${rel}: não inventariado`);
    if (file.endsWith(".json") || file.endsWith(".webmanifest")) JSON.parse(fs.readFileSync(file, "utf8"));
    const allowedExample = rel === "aplicacao/tests/fixtures/test_sample.xlsx" || rel === "aplicacao/.env.example";
    if (!allowedExample && (/\.(?:xlsx?|xlsm|db|sqlite3?|log|pem|key|pfx)$/i.test(file) || path.basename(file).startsWith(".env"))) failures.push(`${rel}: extensão excluída`);
}
if (failures.length) {
    console.error(failures.join("\n"));
    process.exit(1);
}
console.log(`Integridade: ${manifest.files.length} ficheiros conferidos; JSON válido; sem ficheiros inesperados.`);
if (process.argv.includes("--integridade")) process.exit(0);
for (const file of files.filter(file => /\.(?:js|cjs)$/.test(file))) {
    const result = spawnSync(process.execPath, ["--check", file], { cwd: root, encoding: "utf8", timeout: 15000, windowsHide: true });
    if (result.status !== 0) { console.error(result.stderr || result.error); process.exit(1); }
}
console.log("Sintaxe JavaScript: válida.");
const testFiles = files.filter(file => file.endsWith(".test.cjs")).sort();
const tests = spawnSync(process.execPath, ["--require", path.join(__dirname, "bloquear-rede.cjs"), "--test", ...testFiles], { cwd: root, encoding: "utf8", timeout: 60000, windowsHide: true });
process.stdout.write(tests.stdout || "");
process.stderr.write(tests.stderr || "");
if (tests.error) console.error(tests.error.message);
process.exit(tests.status === 0 ? 0 : 1);
