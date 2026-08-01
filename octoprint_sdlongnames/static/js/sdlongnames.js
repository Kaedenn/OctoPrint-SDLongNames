$(function () {
    function SDLongNamesViewModel(parameters) {
        const self = this;

        self.filesViewModel = parameters[0];

        self.onAfterBinding = function () {
            console.log(
                "SDLongNames loaded",
                self.filesViewModel
            );
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: SDLongNamesViewModel,
        dependencies: ["filesViewModel"],
        elements: []
    });
});
