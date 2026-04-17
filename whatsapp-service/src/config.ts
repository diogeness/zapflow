export const config = {
  port: parseInt(process.env.PORT || "3100", 10),
  apiKey: process.env.API_KEY || "",
  webhookUrl: process.env.WEBHOOK_URL || "http://app:8000/api/webhooks/waha",
  sessionsDir: process.env.SESSIONS_DIR || "./data/sessions",
  mediaDir: process.env.MEDIA_DIR || "./data/media",
};
