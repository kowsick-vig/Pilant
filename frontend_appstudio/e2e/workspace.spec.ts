import { test, expect, Page } from "@playwright/test";
async function signup(page: Page) {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Create your account", exact: true })
    .click();
  await page.getByLabel("Your name").fill("Alex Morgan");
  await page
    .getByLabel("Username or email")
    .fill(
      "app-user-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6),
    );
  await page
    .getByLabel("Password", { exact: true })
    .fill("local-test-password-123");
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Your apps", exact: true }),
  ).toBeVisible();
}
async function build(page: Page, source: string, name: string, goal: string) {
  await page.getByRole("button", { name: "Build an app", exact: true }).click();
  await page
    .getByRole("button", {
      name: new RegExp("^" + source + " Try with sample data"),
    })
    .click();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("App name", { exact: true }).fill(name);
  await page.getByLabel("Message Pilant AI", { exact: true }).fill(goal);
  await page.getByRole("button", {name:"Send design message"}).click();
  await expect(page.locator(".designer-message.assistant")).toHaveCount(2);
  await page.getByRole("button", { name: "Build my app", exact: true }).click();
  await expect(page.locator(".generated-brand")).toContainText(name);
  await expect(page.locator(".screen-record-count")).toContainText("records");
}
test("software → goal → dedicated app with its own navigation, data and persistent actions", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await signup(page);
  await expect(page.locator(".sidebar")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "All your work", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({
    path: "/private/tmp/pilant-app-studio.png",
    fullPage: true,
  });
  await build(
    page,
    "Jira",
    "Delivery desk",
    "Show blocked work first in a focus screen and let me browse all records in a table",
  );
  await expect(
    page.getByRole("heading", { name: "Needs attention", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".app-nav-item")).toHaveCount(3);
  await page
    .locator(".focus-index>button")
    .filter({ hasText: "Memory leak" })
    .click();
  await page
    .getByRole("combobox", { name: "Change record status" })
    .selectOption("Done");
  await expect(page.getByRole("status")).toContainText("Status saved");
  await page
    .locator(".app-nav-item")
    .filter({ hasText: "All records" })
    .click();
  await page
    .getByRole("textbox", { name: "Search this screen" })
    .fill("ENG-479");
  await page.getByRole("button", { name: "Run search", exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator("tbody")).toContainText("Done");
  const appUrl = page.url();
  await page.reload();
  await expect(page.locator(".generated-brand")).toContainText("Delivery desk");
  await page
    .getByRole("button", { name: "Back to your apps", exact: true })
    .click();
  await expect(page.locator(".built-app-card")).toHaveCount(1);
  await page
    .locator(".built-app-card")
    .getByRole("button", { name: "Open app", exact: true })
    .click();
  await expect(page).toHaveURL(appUrl);
  await page
    .locator(".app-nav-item")
    .filter({ hasText: "All records" })
    .click();
  await expect(page.locator("tbody tr")).toHaveCount(31);
  await page.screenshot({
    path: "/private/tmp/pilant-delivery-app.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
test("same software can produce two separate apps and no mixed-source workspace", async ({
  page,
}) => {
  await signup(page);
  await build(
    page,
    "Jira",
    "Triage desk",
    "Show blocked work in a focus interface",
  );
  const first = page.url();
  await page
    .getByRole("button", { name: "Back to your apps", exact: true })
    .click();
  await build(
    page,
    "Jira",
    "Issue browser",
    "I want a compact table to browse all issues",
  );
  expect(page.url()).not.toBe(first);
  await expect(page.locator(".generated-app")).toHaveClass(/density-compact/);
  await expect(page.locator("table")).toBeVisible();
  await expect(
    page.getByRole("tablist", { name: "Workspace layout" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Back to your apps", exact: true })
    .click();
  await expect(page.locator(".built-app-card")).toHaveCount(2);
});
test("a live software connection is required before describing its app", async ({
  page,
}) => {
  await signup(page);
  await page.getByRole("button", { name: "Build an app", exact: true }).click();
  await page
    .getByRole("button", { name: /^GitHub Connect your existing account/ })
    .click();
  await expect(
    page.getByRole("button", { name: "Continue", exact: true }),
  ).toBeDisabled();
  await page
    .locator(".connection-card")
    .getByRole("button", { name: "Connect", exact: true })
    .click();
  await page.getByLabel("Repository", { exact: true }).fill("bad-repository");
  await page
    .getByRole("button", { name: "Connect securely", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("owner/name");
});
test("mobile generated support app can open a record and switch its own screens", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signup(page);
  await build(
    page,
    "Helpdesk",
    "Support desk",
    "Give me a focused inbox to handle existing support tickets",
  );
  await page.locator(".message-row").first().click();
  await expect(
    page.getByRole("region", { name: "Record details" }),
  ).toBeVisible();
  await page.screenshot({
    path: "/private/tmp/pilant-support-app-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
  ).toBe(false);
  await page
    .getByRole("button", { name: "Close details", exact: true })
    .click();
  await page
    .getByRole("button", { name: "App navigation", exact: true })
    .click();
  await page
    .locator(".app-nav-item")
    .filter({ hasText: "All records" })
    .click();
  await expect(page.locator("table")).toBeVisible();
});

test("failed refinement keeps the existing app and shows an actionable error", async ({
  page,
}) => {
  await signup(page);
  await build(
    page,
    "Jira",
    "My issue desk",
    "Show blocked issues first in a focus view",
  );
  await page
    .getByRole("button", { name: "Adapt this app", exact: true })
    .click();
  await page
    .getByLabel("What would work better?", { exact: true })
    .fill("Make this app a compact table instead");
  await page
    .getByRole("button", { name: "Update this app", exact: true })
    .click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "Your current app is unchanged",
  );
  await page.getByRole("button", { name: "Close dialog", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Needs attention", exact: true }),
  ).toBeVisible();
});


test("Jira copilot answers questions and follows up with real matching sample records", async ({page}) => {
  await signup(page);
  await build(page, "Jira", "Copilot desk", "Show all issues in a table");
  await page.getByRole("button", {name:"Show blocked issues assigned to Priya", exact:true}).click();
  await expect(page.locator(".copilot-count")).toHaveText("2");
  await expect(page.locator("tbody")).toContainText("PIL-101");
  await expect(page.locator("tbody")).toContainText("PIL-102");
  await page.getByLabel("Ask Pilant Copilot", {exact:true}).fill("Only the highest priority ones");
  await page.getByRole("button", {name:"Send copilot question"}).click();
  await expect(page.locator(".copilot-count")).toHaveText("1");
  await expect(page.locator("tbody")).toContainText("PIL-101");
  await page.getByLabel("Ask Pilant Copilot", {exact:true}).fill("Show PIL-104 and why it is blocked");
  await page.getByRole("button", {name:"Send copilot question"}).click();
  await expect(page.locator("tbody")).toContainText("Warehouse API test access is pending");
  await page.screenshot({path:"/private/tmp/pilant-copilot-desktop.png",fullPage:true});
  await page.reload();
  await expect(page.locator(".copilot-question")).toHaveCount(3);
  await page.getByRole("button", {name:"Clear copilot conversation"}).click();
  await expect(page.locator(".copilot-welcome")).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await page.reload();
  await page.getByRole("button", {name:"Pilant Copilot",exact:true}).click();
  await page.getByRole("button", {name:"Show blocked issues assigned to Priya",exact:true}).click();
  await expect(page.locator(".copilot-count")).toHaveText("2");
  await page.screenshot({path:"/private/tmp/pilant-copilot-mobile.png",fullPage:true});
  await page.getByRole("button", {name:"Close copilot",exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("shared copilot in Helpdesk and all live app interfaces", async ({page}) => {
  await signup(page);
  await build(page, "Helpdesk", "Support copilot", "Show all tickets in a table");
  await page.getByRole('button',{name:'Show all records',exact:true}).click();
  await expect(page.locator('.copilot-count')).not.toHaveText('0');
  await expect(page.locator('.copilot-source')).toContainText('Helpdesk');
  for (const source of ['github','gmail','slack']) {
    await page.route('**/api/apps/*', async route => {
      const response=await route.fetch();
      const body=await response.json();
      if(body.app) body.app.source=source;
      await route.fulfill({response,json:body});
    });
    await page.reload();
    await expect(page.getByRole('button',{name:'Pilant Copilot',exact:true})).toBeVisible();
    await expect(page.locator('.copilot-source')).toContainText('Connected data');
    await expect(page.locator('.copilot-source')).not.toContainText('Jira');
    await page.getByLabel('Ask Pilant Copilot',{exact:true}).fill('Show all records');
    await page.getByRole('button',{name:'Send copilot question'}).click();
    await expect(page.getByRole('heading',{name:'Copilot results',exact:true})).toBeVisible();
    await page.unroute('**/api/apps/*');
  }
});


test("design conversation follows up, persists and builds from the agreed brief", async ({page}) => {
 await signup(page);
 await page.getByRole('button',{name:'Build an app',exact:true}).click();
 await page.getByRole('button',{name:/^Jira Try with sample data/}).click();
 await page.getByRole('button',{name:'Continue',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Let’s build your app.'})).toBeVisible();
 await page.getByLabel('Message Pilant AI').fill('Show blocked work first');
 await page.getByRole('button',{name:'Send design message'}).click();
 await expect(page.locator('.designer-thread')).toContainText('What should the interface look like');
 await page.getByRole('button',{name:'Use a compact table for comparison',exact:true}).click();
 await expect(page.locator('.designer-thread')).toContainText('How do you want to work each day');
 await expect(page.locator('.designer-plan')).toContainText('blocked work');
 await expect(page.locator('.designer-plan')).toContainText('compact table');
 await page.reload();
 await expect(page.locator('.designer-message.user')).toHaveCount(2);
 await page.screenshot({path:'/private/tmp/pilant-designer-chat.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
 await page.screenshot({path:'/private/tmp/pilant-designer-chat-mobile.png',fullPage:true});
 await page.getByRole('button',{name:'Build my app',exact:true}).click();
 await expect(page.locator('.generated-brand')).toContainText('My Jira app');
 await expect(page.locator('.generated-app')).toHaveClass(/density-compact/);
});
