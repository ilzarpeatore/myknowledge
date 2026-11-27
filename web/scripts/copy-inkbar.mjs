// Copies the PDF.js files the PDF viewer (Inkbar 0.18, which draws with PDF.js) loads at
// runtime into public/inkbar/<pdfjs version>/: the worker, the character maps and
// standard fonts (fetched only when a page needs them), the image decoders
// (WebAssembly) and colour profiles, with PDF.js's licence. Runs before `next dev` and
// `next build`. The folder is named for the version so a cached file is never stale.
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";

const pdfjs = join(process.cwd(), "node_modules", "pdfjs-dist");
const version = JSON.parse(readFileSync(join(pdfjs, "package.json"), "utf8")).version;
const out = join(process.cwd(), "public", "inkbar");
const dest = join(out, version);

rmSync(out, { recursive: true, force: true }); // drop older versions
mkdirSync(dest, { recursive: true });
for (const [from, to, required] of [
  ["build/pdf.worker.min.mjs", "pdf.worker.min.mjs", true],
  ["cmaps", "cmaps", true],
  ["standard_fonts", "standard_fonts", true],
  ["wasm", "wasm", true],
  ["iccs", "iccs", false],
  ["LICENSE", "LICENSE", false],
]) {
  const src = join(pdfjs, from);
  if (!existsSync(src)) {
    if (required) throw new Error(`copy-inkbar: falta pdfjs-dist/${from}`);
    continue;
  }
  cpSync(src, join(dest, to), { recursive: true });
}
console.log(`copy-inkbar: archivos del visor PDF en public/inkbar/${version}/`);
