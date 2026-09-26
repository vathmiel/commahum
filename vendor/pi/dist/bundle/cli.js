#!/nix/store/v6wsd9nyglmwh32yn7w8z11dq9cksg4m-nodejs-24.20.0/bin/node
import { createRequire, enableCompileCache } from "node:module";

enableCompileCache();
createRequire(import.meta.url)("./cli-runtime.js");
