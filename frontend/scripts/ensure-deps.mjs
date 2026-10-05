import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

const requiredFiles = [
  'node_modules/.package-lock.json',
  'node_modules/typescript/bin/tsc',
  'node_modules/vite/bin/vite.js',
];

if (requiredFiles.every((file) => existsSync(resolve(file)))) {
  console.log('Frontend dependencies are installed.');
  process.exit(0);
}

console.log('Frontend dependencies are missing or incomplete; running npm ci.');
const npmCli = process.env.npm_execpath;
const command = npmCli ? process.execPath : process.platform === 'win32' ? 'npm.cmd' : 'npm';
const args = npmCli ? [npmCli, 'ci'] : ['ci'];
const result = spawnSync(command, args, { stdio: 'inherit', shell: !npmCli && process.platform === 'win32' });

if (result.error) {
  console.error(`Unable to install frontend dependencies: ${result.error.message}`);
  process.exit(1);
}

process.exit(result.status ?? 1);