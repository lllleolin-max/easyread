const appId = "com.easyread.foldersync.preview";
const productName = "EasyRead 文件夹同步试用版";

module.exports = {
  extends: null,
  appId,
  productName,
  asar: true,
  publish: null,
  protocols: [],
  directories: { output: "dist/folder-sync-preview" },
  extraMetadata: {
    name: "easyread-folder-sync-preview",
    desktopPreview: { appId, title: productName, profile: "EasyReadFolderSyncPreview" },
  },
  files: ["electron/**/*", "package.json"],
  extraResources: [{ from: "build/backend", to: "backend" }],
  win: { icon: "electron/assets/icon.ico", executableName: "EasyReadFolderSyncPreview" },
  nsis: {
    oneClick: true,
    perMachine: false,
    shortcutName: productName,
    include: "installer/folder-sync-preview.nsh",
    createDesktopShortcut: true,
    deleteAppDataOnUninstall: false,
    artifactName: "EasyRead-Folder-Sync-Preview-${version}-Windows-${arch}-Setup.${ext}",
  },
};
