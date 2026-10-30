const fs = require('fs');
const zlib = require('zlib');
const path = process.argv[2];
const tailChars = Number(process.argv[3] || 4000);
const buf = fs.readFileSync(path);
let text;
try {
  text = zlib.zstdDecompressSync(buf).toString('utf8');
} catch (e) {
  console.log('zstd error:', e.message);
  process.exit(1);
}
console.log('decompressed chars:', text.length);
console.log('--- TAIL ---');
console.log(text.slice(-tailChars));
