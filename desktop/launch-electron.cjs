const { spawn } = require("child_process");
const path = require("path");

const electron = require("electron");
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

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
