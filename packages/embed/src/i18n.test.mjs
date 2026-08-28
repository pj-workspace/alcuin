import assert from "node:assert/strict";

import { getEmbedMessages, resolveEmbedLocale } from "./i18n.ts";

assert.equal(resolveEmbedLocale(), "en");
assert.equal(resolveEmbedLocale("en-US"), "en");
assert.equal(resolveEmbedLocale("zh-CN"), "zh");
assert.equal(resolveEmbedLocale("ZH-Hant"), "zh");
assert.equal(getEmbedMessages("en").connected, "Connected");
assert.equal(getEmbedMessages("zh-CN").connected, "已连接");
assert.equal(getEmbedMessages("zh-CN").usingTool("knowledge.search"), "正在使用 knowledge.search");

console.log("embed i18n tests passed");
