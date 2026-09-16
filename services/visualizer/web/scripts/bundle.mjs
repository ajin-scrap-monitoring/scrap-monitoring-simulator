import { cp, mkdir } from "node:fs/promises";

await mkdir("dist/assets/vendor", { recursive: true });
await cp("node_modules/three/build/three.module.js", "dist/assets/vendor/three.module.js");
await cp("src/index.html", "dist/index.html");
await cp("src/style.css", "dist/style.css");
