const { spawn } = require("child_process");
const path = require("path");

const electron = require("electron");
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const child = spawn(electron, [path.join(__dirname)], {
  stdio: "inherit",
  env,
  windowsHide: false,
});

child.on("exit", (code) => process.exit(code ?? 0));
child.on("error", (error) => {
  console.error(error);
  process.exit(1);
});
