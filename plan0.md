# plan0

our system will manage and control wiki systems research project based on the cluade plugin that build wiki project to integreate with obsidian. see that ref project at https://github.com/AgriciDaniel/claude-obsidian

you need to clone and read all reference project to .ref folder and use as needed.


## important reference
* https://github.com/SOLEROM/agent_ccpanBuilder - help skill to build web based tui controls.
* https://github.com/SOLEROM/cldlab - my loved features: bash inside terminal with push button to spin cluade code or bash in projecet /edit md files on the web and store them / yaml config of displayed system.


## general
* each research has it own root folder - we call it the vault - as that it the project root that obsidian will open as wiki root.
* our system will help us manage command control and use multiple research vaults. 
* our system will have option to edit and save the yaml file for our configuration.
* also our system will have a doc folder that explain about our system that will be displayed on the web system and can edited and updated.

## the plugin
read the ref plugin notes:
* obsiPlug/install.md  - how we install the tool
* obsiPlug/commands.md  - common commands of the plugin
* obsiPlug/usageExampels.md - examples how to add data to each vault.

## tools
* tools folder will held our script that we can use 
* it is best to held here generel tool that can run on differnet vaults.
* for example tools/injest.sh will call with url and vault to injest and add the url data to that vault.

## web tui
* our system will be able to spin a web based linux command line in the web - calling that a web tui.
* see all the nice feature we build to control that windows on the cldlab ref project.

## system features
* our web system will manage all the vaults under the defined top-root path.
* we will be able to call and order different operation on each or all vaults
* operation could be run as immediate and blocking or background non blocking or due to be run in differnt time.
* as we run our task using cluade code we need to take to effect the window context time and limits. we have limit to per day windows and for the week.
* to "do latter" task we can assing time for the window or pop it to some list of low prioraty tasks.
* all task will be with prioraty.
* our system can decide when to run low prioraty task - or in the non import time limit like at night or collect enoght task to run in some unused windows. or leave it to the last windows of the week to make effective of unused windows.
* all task will be accessible on out list so we can spin them on demand before the window if desire.
* all running task will be save with log or some process so we can test the runs and result code to make sure that task completed. 
* task will held state so completed task can be resping again if needed.
* we will have option to add cron task to run on our scedula. that will be mainly used for house keeping link and order task.
* all options and params will be held in yaml files under config folders.


## operation
* our system will be able to run new and scedula task per vault or to all vault.
* our system will be able to spin and create new folder research based on wikValTemplate (clone of my other seperated repo : https://github.com/SOLEROM/wikValTemplate) folder and by calling the correct toll on the plugin.
* our system will show us all available vaults/research project and we can spin the claude code inside that research folder ( see labcld config yaml that define the run command ).
* in the spined shell project we can run tool and plugin for that research so updated content will be in the right folder of the project.
* our system can call a trigger to open obsidian for the project.