// 构建 Python sidecar（PyInstaller）。
//
// 用法：node packaging/build-sidecar.mjs
// 配置：packaging/sidecar-build.json（python 路径、排除模块等）
// 环境变量 DSH_PYTHON 可覆盖 python 路径。
//
// 退出码非 0 时，调用方（pnpm dist:win）会停止，避免把旧 sidecar 打进安装包。

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const configPath = path.join(here, "sidecar-build.json");

const config = JSON.parse(readFileSync(configPath, "utf8"));
const python = process.env.DSH_PYTHON || config.python;

if (!existsSync(python)) {
  console.error(`找不到打包用 Python: ${python}`);
  console.error("可设置 DSH_PYTHON 指向一个已安装依赖的解释器。");
  process.exit(1);
}

const args = [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onedir",
  "--name",
  config.name,
  "--distpath",
  path.join(root, config.distpath),
  "--workpath",
  path.join(root, config.workpath),
  "--specpath",
  path.join(root, config.specpath),
];

for (const moduleName of config.collectSubmodules) {
  args.push("--collect-submodules", moduleName);
}
for (const dataName of config.collectData) {
  args.push("--collect-data", dataName);
}
for (const hidden of config.hiddenImports) {
  args.push("--hidden-import", hidden);
}
for (const excluded of config.excludeModules) {
  args.push("--exclude-module", excluded);
}
args.push(config.entry);

console.log(`[sidecar] ${python} -m PyInstaller ... (${config.excludeModules.length} 个排除模块)`);
const result = spawnSync(python, args, { cwd: root, stdio: "inherit" });

if (result.error) {
  console.error(`[sidecar] 启动 PyInstaller 失败: ${result.error.message}`);
  process.exit(1);
}
if (result.status !== 0) {
  console.error(`[sidecar] PyInstaller 失败，退出码 ${result.status}`);
  process.exit(result.status ?? 1);
}

const exe = path.join(root, config.distpath, config.name, `${config.name}.exe`);
if (!existsSync(exe)) {
  console.error(`[sidecar] 未生成可执行文件: ${exe}`);
  process.exit(1);
}
console.log(`SIDECAR_OK ${exe}`);
