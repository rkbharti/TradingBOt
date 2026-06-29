module.exports = {
  apps: [
    {
      name: "dashboard",
      script: ".venv/Scripts/python.exe",
      args: "apps/dashboard/main.py",
      interpreter: "none",
      watch: false,
      env: {
        NODE_ENV: "production"
      }
    },
    {
      name: "bot_xauusd",
      script: ".venv/Scripts/python.exe",
      args: "apps/trader/main.py",
      interpreter: "none",
      watch: false,
      env: {
        SYMBOL: "XAUUSD",
        CONTROL_PORT: 5001,
        RISK_PER_TRADE: "0.25"
      }
    },
    {
      name: "bot_xagusd",
      script: ".venv/Scripts/python.exe",
      args: "apps/trader/main.py",
      interpreter: "none",
      watch: false,
      env: {
        SYMBOL: "XAGUSD",
        CONTROL_PORT: 5002,
        RISK_PER_TRADE: "0.25"
      }
    },
    {
      name: "bot_eurusd",
      script: ".venv/Scripts/python.exe",
      args: "apps/trader/main.py",
      interpreter: "none",
      watch: false,
      env: {
        SYMBOL: "EURUSD",
        CONTROL_PORT: 5003,
        RISK_PER_TRADE: "0.25"
      }
    },
    {
      name: "bot_us30",
      script: ".venv/Scripts/python.exe",
      args: "apps/trader/main.py",
      interpreter: "none",
      watch: false,
      env: {
        SYMBOL: "US30",
        CONTROL_PORT: 5004,
        RISK_PER_TRADE: "0.25"
      }
    }
  ]
};
