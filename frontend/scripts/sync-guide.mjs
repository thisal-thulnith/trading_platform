// Copies the platform guide (docs/guide: the Markdown source, its PDF and its pictures) into public/guide so the
// in-app Guide page (/guide) serves exactly the same text as the PDF. Runs before every build (npm "prebuild").
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "docs", "guide");
const dst = join(here, "..", "public", "guide");
if (!existsSync(join(src, "PLATFORM_GUIDE.md"))) {
  console.warn(`sync-guide: no guide at ${src}; /guide will show "not found"`);
  process.exit(0);
}
rmSync(dst, { recursive: true, force: true });
mkdirSync(dst, { recursive: true });
for (const f of ["PLATFORM_GUIDE.md", "Platform-Guide.pdf", "images"]) {
  if (existsSync(join(src, f))) cpSync(join(src, f), join(dst, f), { recursive: true });
}
console.log(`sync-guide: copied the guide to ${dst}`);
