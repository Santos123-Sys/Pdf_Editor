import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const fixture=path.join(process.cwd(),"tests","fixtures","two-pages.pdf");
test("upload, preview every page, rotate, download, and signer consent",async({page,browser})=>{
  await page.goto("/");
  await page.getByLabel("Add documents").setInputFiles({name:"browser-happy.pdf",mimeType:"application/pdf",buffer:fs.readFileSync(fixture)});
  await expect(page.getByRole("heading",{name:"browser-happy.pdf",exact:true})).toBeVisible();
  await expect(page.getByAltText("PDF page 1")).toBeVisible();
  await page.getByRole("button",{name:"Next",exact:true}).click();
  await expect(page.getByAltText("PDF page 2")).toBeVisible();
  await page.getByRole("button",{name:"Create rotate version"}).click();
  await expect(page.getByRole("status").filter({hasText:"Version 1 saved"})).toBeVisible();
  const downloaded=page.waitForEvent("download");
  await page.getByRole("link",{name:"↓ Download",exact:true}).click();
  expect((await downloaded).suggestedFilename()).toBe("browser-happy-v1.pdf");
  await page.getByLabel("Signer name",{exact:true}).fill("Jane Doe");
  await page.getByRole("button",{name:"Create invitation link"}).click();
  const url=await page.getByLabel("Invitation link",{exact:true}).inputValue();
  const signerContext=await browser.newContext(); // No workspace credentials for an invited signer.
  const signer=await signerContext.newPage();
  await signer.goto(url);
  await expect(signer.getByText("You were invited as")).toBeVisible();
  await expect(signer.getByAltText("PDF page 1")).toBeVisible();
  await signer.getByRole("button",{name:"Next",exact:true}).click();
  await expect(signer.getByAltText("PDF page 2")).toBeVisible();
  const original=await signerContext.request.get(url.replace("/sign/","/api/invitations/")+"/file");
  expect(original.status()).toBe(200);
  if(process.env.TEST_WORKSPACE_PASSWORD){
    const blocked=await signerContext.request.get(new URL("/api/documents",url).toString());
    expect(blocked.status()).toBe(401);
    const bypass=await signerContext.request.get(new URL("/api/documents",url).toString(),{headers:{"x-middleware-subrequest":"middleware:middleware:middleware:middleware:middleware"}});
    expect(bypass.status()).toBe(401);
  }
  await signer.getByLabel("Your full name").fill("Jane Doe");
  await signer.getByRole("checkbox").check();
  await signer.getByRole("button",{name:"Consent and seal"}).click();
  await expect(signer.getByRole("status").filter({hasText:"Sealed successfully"})).toBeVisible();
  await expect(signer.getByRole("link",{name:"Download sealed PDF"})).toBeVisible();
  await signer.screenshot({path:"test-results/signer-sealed.png",fullPage:true});
  await signerContext.close();
  await page.reload();
  await page.getByRole("button").filter({has:page.getByText("browser-happy.pdf",{exact:true})}).click();
  await expect(page.getByText("signature sealed",{exact:true})).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:"test-results/workspace-mobile.png",fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
});

test("unavailable API has a recoverable error",async({page})=>{
  await page.route("**/api/documents",route=>route.fulfill({status:503,contentType:"application/json",body:JSON.stringify({detail:"Document service is unavailable. Please retry shortly."})}));
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("unavailable");
  await expect(page.getByRole("button",{name:"Refresh workspace"})).toBeVisible();
});
