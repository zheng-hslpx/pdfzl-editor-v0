import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const defaultProjectRoot = resolve(scriptDirectory, "..");
const resourceSets = [
  { name: "cmaps", source: "cmaps", local: "public/cmaps", requiredLicenses: ["LICENSE"] },
  {
    name: "standard_fonts",
    source: "standard_fonts",
    local: "public/standard_fonts",
    requiredLicenses: ["LICENSE_FOXIT", "LICENSE_LIBERATION"],
  },
  {
    name: "wasm",
    source: "wasm",
    local: "public/wasm",
    requiredLicenses: [
      "LICENSE_JBIG2", "LICENSE_OPENJPEG", "LICENSE_QCMS",
      "LICENSE_PDFJS_JBIG2", "LICENSE_PDFJS_OPENJPEG", "LICENSE_PDFJS_QCMS",
    ],
  },
];

async function listRegularFiles(root, directory) {
  const result = [];
  async function visit(current, prefix) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
      const fullPath = join(current, entry.name);
      if (entry.isDirectory()) await visit(fullPath, relativePath);
      else if (entry.isFile()) result.push(relativePath);
      else throw new Error(`unsupported resource entry: ${relative(root, fullPath).split(sep).join("/")}`);
    }
  }
  await visit(directory, "");
  return result.sort();
}

async function hashFile(path) {
  return createHash("sha256").update(await readFile(path)).digest("hex");
}

export async function checkPdfjsResources(projectRoot = defaultProjectRoot) {
  const root = resolve(projectRoot);
  const errors = [];
  let lockedVersion = "unknown";
  let installedVersion = "unknown";
  const lockPath = join(root, "package-lock.json");
  const installedPackagePath = join(root, "node_modules", "pdfjs-dist", "package.json");

  try {
    const lock = JSON.parse(await readFile(lockPath, "utf8"));
    lockedVersion = lock.packages?.["node_modules/pdfjs-dist"]?.version ?? "unknown";
    if (lockedVersion === "unknown") errors.push("package-lock.json has no node_modules/pdfjs-dist version");
  } catch (error) {
    errors.push(`cannot read package-lock.json: ${error.message}`);
  }

  try {
    const installed = JSON.parse(await readFile(installedPackagePath, "utf8"));
    installedVersion = installed.version ?? "unknown";
    if (installedVersion !== lockedVersion) {
      errors.push(`installed pdfjs-dist ${installedVersion} does not match lockfile ${lockedVersion}`);
    }
  } catch (error) {
    errors.push(`cannot read installed pdfjs-dist package: ${error.message}`);
  }

  const counts = {};
  for (const set of resourceSets) {
    const sourceDirectory = join(root, "node_modules", "pdfjs-dist", set.source);
    const localDirectory = join(root, set.local);
    let sourceFiles;
    let localFiles;
    try {
      sourceFiles = await listRegularFiles(root, sourceDirectory);
    } catch (error) {
      errors.push(`cannot enumerate package ${set.source}: ${error.message}`);
      continue;
    }
    try {
      localFiles = await listRegularFiles(root, localDirectory);
    } catch (error) {
      errors.push(`cannot enumerate ${set.local}: ${error.message}`);
      continue;
    }

    counts[set.name] = localFiles.length;
    const sourceSet = new Set(sourceFiles);
    const localSet = new Set(localFiles);
    for (const file of sourceFiles) {
      if (!localSet.has(file)) errors.push(`${set.local}/${file} is missing`);
    }
    for (const file of localFiles) {
      if (!sourceSet.has(file)) errors.push(`${set.local}/${file} is not present in locked pdfjs-dist`);
    }
    for (const file of set.requiredLicenses) {
      if (!sourceSet.has(file)) errors.push(`locked pdfjs-dist is missing required license ${set.source}/${file}`);
      if (!localSet.has(file)) errors.push(`${set.local}/${file} is missing`);
    }

    for (const file of sourceFiles) {
      if (!localSet.has(file)) continue;
      const sourceHash = await hashFile(join(sourceDirectory, file));
      const localHash = await hashFile(join(localDirectory, file));
      if (sourceHash !== localHash) errors.push(`${set.local}/${file} SHA-256 differs from locked pdfjs-dist`);
    }
  }

  return { root, lockedVersion, installedVersion, counts, errors };
}

async function main() {
  const rootIndex = process.argv.indexOf("--root");
  const projectRoot = rootIndex >= 0 ? process.argv[rootIndex + 1] : defaultProjectRoot;
  if (rootIndex >= 0 && !projectRoot) {
    console.error("usage: node scripts/check-pdfjs-resources.mjs [--root <frontend-project-root>]");
    process.exitCode = 2;
    return;
  }

  const result = await checkPdfjsResources(projectRoot);
  if (result.errors.length) {
    console.error(`PDF.js resource check failed for pdfjs-dist ${result.lockedVersion}:`);
    for (const error of result.errors) console.error(`- ${error}`);
    process.exitCode = 1;
    return;
  }

  console.log(`PDF.js resources match locked pdfjs-dist ${result.lockedVersion}.`);
  for (const [name, count] of Object.entries(result.counts)) {
    console.log(`- ${name}: ${count} files verified, including required license files`);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  void main();
}
