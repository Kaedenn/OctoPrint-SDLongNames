$(function () {
    function SDLongNamesViewModel(parameters) {
        const self = this;

        self.filesViewModel = parameters[0];
        self.refreshQueued = false;

        function refreshFileList() {
            self.refreshQueued = false;
            self.filesViewModel.requestData({force: true});
        }

        self.onDataUpdaterPluginMessage = function (plugin, message) {
            if (
                plugin !== "sdlongnames" ||
                !message ||
                message.type !== "refresh_files" ||
                self.refreshQueued
            ) {
                return;
            }

            self.refreshQueued = true;

            const currentRequest =
                self.filesViewModel._otherRequestInProgress;

            if (
                currentRequest !== undefined &&
                typeof currentRequest.always === "function"
            ) {
                currentRequest.always(refreshFileList);
            } else {
                refreshFileList();
            }
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: SDLongNamesViewModel,
        dependencies: ["filesViewModel"],
        elements: []
    });
});
