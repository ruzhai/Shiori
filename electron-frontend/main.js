const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn, execSync } = require('child_process');

let pythonProcess = null;
let backendStarting = false;

function killOldBackend() {
  try {
    if (process.platform === 'win32') {
      execSync('taskkill /f /im python.exe 2>nul & taskkill /f /im python3.exe 2>nul & taskkill /f /im python3.12.exe 2>nul', { stdio: 'ignore' });
    } else {
      execSync('pkill -f api_server.py 2>/dev/null || true', { stdio: 'ignore' });
    }
  } catch (_) {}
}

function waitForBackend(url, retries, callback) {
  const http = require('http');
  http.get(url, (res) => {
    callback(null);
  }).on('error', () => {
    if (retries <= 0) return callback(new Error('Backend not ready'));
    setTimeout(() => waitForBackend(url, retries - 1, callback), 500);
  });
}

function startPythonBackend(callback) {
  if (backendStarting) return;
  backendStarting = true;

  killOldBackend();

  const pythonPath = path.join(__dirname, '..', 'api_server.py');
  pythonProcess = spawn('python', [pythonPath], { stdio: 'pipe' });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`Python: ${data}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`Python Error: ${data}`);
  });

  pythonProcess.on('error', (err) => {
    console.error('Failed to start Python:', err.message);
    backendStarting = false;
  });

  pythonProcess.on('close', () => {
    pythonProcess = null;
    backendStarting = false;
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });

  waitForBackend('http://localhost:5000/api/test', 0, (err) => {
    if (err) startPythonBackend();
  });
  waitForBackend('http://localhost:5000/api/test', 20, (err) => {
    if (err) console.error('Backend failed to start');
    win.loadFile('index.html');
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (pythonProcess) {
    pythonProcess.kill();
  }
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
