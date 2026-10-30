const { spawn } = require("child_process");
const path = require("path");

const electron = require("electron");
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

// 工作区被沙箱标成低完整性时（例如 DSH），Chromium 沙箱无法初始化，
// Electron 会以 0x80000003 直接退出。打包版同样使用 --no-sandbox，这里保持一致。
const args = ["--no-sandbox", "--disable-gpu"];
if (process.env.DSH_ELECTRON_DEBUG === "1") {
  args.push("--remote-debugging-port=9222");
}
args.push(path.join(__dirname));

const child = spawn(electron, args, {
  stdio: "inherit",
  env,
  windowsHide: false,
});

child.on("exit", (code) => process.exit(code ?? 0));
child.on("error", (error) => {
  console.error(error);
  process.exit(1);
});
