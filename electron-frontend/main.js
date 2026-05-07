const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let pythonProcess = null;

function createWindow() {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });

  win.loadFile('index.html');

  // 启动 Python 后端
  startPythonBackend();
}

function startPythonBackend() {
  const pythonPath = path.join(__dirname, '..', 'api_server.py');
  pythonProcess = spawn('python', [pythonPath]);

  pythonProcess.stdout.on('data', (data) => {
    console.log(`Python: ${data}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`Python Error: ${data}`);
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
