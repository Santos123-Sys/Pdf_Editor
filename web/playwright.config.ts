import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: {timeout: 15_000},
  use: {
    baseURL: process.env.TEST_WEB_URL || "http://127.0.0.1:3000",
    httpCredentials: process.env.TEST_WORKSPACE_PASSWORD ? {username:"workspace",password:process.env.TEST_WORKSPACE_PASSWORD} : undefined,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  reporter: "list",
});
