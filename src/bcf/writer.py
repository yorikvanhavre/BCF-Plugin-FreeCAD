import os
import io # used for writing files in utf8
import sys
from uuid import UUID
from collections import deque

if __name__ == "__main__":
    sys.path.insert(0, "/home/patrick/projects/freecad/plugin/src")
    print(sys.path)
    import copy as c
import xml.etree.ElementTree as ET
import xml.dom.minidom as MD
import bcf.reader as reader
import interfaces.hierarchy as iH
import interfaces.state as iS
import interfaces.identifiable as iI
import bcf.markup as m
import bcf.project as p
import bcf.uri as u

"""
`elementHierarchy` contains for each element, the writer supports writing, the
hierarchy of the element in its corresponding XML file. Thereby hierarchy is
defined to be the sequence of parents till the root element of the XML document
is reached.
This information is used for adding new elements to the existing XML file.
Keys that contain `@` as second character are attributes that can be changed or
added, all other keys correspond to acutal elements in the XML file.
The first character of every element is the first letter in the name of the
containing element.

"""
elementHierarchy = {"Comment": ["Comment", "Markup"],
    "MViewpoints": ["Viewpoint", "Markup"],
    "TDocumentReference": ["DocumentReference", "Topic", "Markup"],
    "MTopic": ["Topic", "Markup"],
    "TLastModifiedDate": ["LastModifiedDate", "Topic", "Markup"],
    "TLastModifiedAuthor": ["LastModifiedAuthor", "Topic", "Markup"],
    "CLastModifiedDate": ["LastModifiedDate", "Comment", "Markup"],
    "CLastModifiedAuthor": ["LastModifiedAuthor", "Comment", "Markup"],
    "TStage": ["Stage", "Topic", "Markup"],
    "TDueDate": ["DueDate", "Topic", "Markup"],
    "TLabels": ["Labels", "Topic", "Markup"],
    "T@TopicStatus": ["@TopicStatus", "Topic", "Markup"],
    "T@TopicType": ["@TopicType", "Topic", "Markup"],
    }


"""
`elementOrder` contains the relative order of elements in each changeable
parent element. `Comment`, for example, is changeable, but according to the
definition a viewpoint (whose corresponding XML element is `VisualizationInfo`
in `viewpoint.bcfv`) is not changeable, so it doesn't show up in the list
here.
A sequence is defined to be order of elements that are part of a complex type.
For example `Comment` is part of the complex type `Markup`. The sequence of
elements for `Markup` is now: 'Header'->'Topic'->'Comment'->'Viewpoints',
therefore, given `Markup` is defined complete, `Comment` will be third to find
in `Markup`.
"""
elementOrder = {"Markup": ["Header", "Topic", "Comment", "Viewpoints"],
        "Topic": ["ReferenceLink", "Title", "Priority", "Index", "Labels",
            "CreationDate", "CreationAuthor", "ModifiedDate", "ModifiedAuthor",
            "DueDate", "AssignedTo", "Stage", "Description", "BimSnippet",
            "DocumentReference", "RelatedTopic"],
        "Comment": ["Date", "Author", "Comment", "Viewpoint", "ModifiedDate",
            "ModifiedAuthor"],
        "Header": ["File"],
        "File": ["Filename", "Date", "Reference"]
        }


"""
A list of elements that can occur multiple times in the corresponding XML file
"""
listElements = ["Comment", "DocumentReference", "RelatedTopic", "Labels"]


"""
An ordered list of tuples. Every tuple element denotes an addition,
modification or deletion of exactly one object in the project. A tuple thereby
consists of an project object, the object in question and a third value holding
the old value iff the object shall be modified, otherwise this will be `None`.
It is assumed that as soon as a new entry is added to the list, nothing holds
the reference to the contents of the project object anymore. The project and
object shall therefore be deep copies of the original state.
Following a schematic element is depicted:

    projectUpdats[x] = (project, element, prevVal)

This list will contain all updates that were not processed.
"""
projectUpdates = list()

"""
An ordered list of five elements at most. Every element is a tuple previously
held by `projectUpdates`. Every element of former list is, as soon as it is
processed, appended to this list.
It therefore serves as storage past plugin states and enables undo operations.
"""
SNAPSHOT_CNT = 5
projectSnapshots = deque([None]*SNAPSHOT_CNT, SNAPSHOT_CNT)


################## DEPRECATED ##################
"""
Contains elements that have the state iS.State.States.ADDED. This list gets
filled by writer.compileChanges() and the elements get consumed by
writer.addElement()
"""
addedObjects = list()

"""
Contains elements that have the state iS.State.States.DELETED. This list gets
filled by writer.compileChanges() and the elements get consumed by
writer.deleteElement()
"""
deletedObjects = list()

"""
Contains elements that have the state iS.State.States.MODIFIED. This list gets
filled by writer.compileChanges() and the elements get consumed by
writer.modifyElement()
"""
modifiedObjects = list()
################################################


def getUniqueIdOfListElementInHierarchy(element):

    """
    Looks through the hierarchy of an object up to the project. If somewhere on
    the way up an element is identified as list element (may occur more than
    once inside the same XML element) then the id of that element is returned.
    It is assumed that max. one such list element is found. If `element` itself
    is a list element `None` is returned. If no list element is found `None` is
    returned.
    """

    elementHierarchy = iH.Hierarchy.checkAndGetHierarchy(element)
    if not elementHierarchy:
        return None

    listElement = None
    # climb up the hierarchy starting with the direct parent
    for item in elementHierarchy[1:]:
        if item.__class__.__name__ in listElements:
            listElement = item
            p.debug("{} is a list element!".format(
                    getUniqueIdOfListElementInHierarchy.__name__,
                        item))
            break

    if isinstance(listElement, iI.XMLIdentifiable):
        p.debug("its id = {} element!".format(
                getUniqueIdOfListElementInHierarchy.__name__,
                    item.xmlId))
        return item.xmlId
    return None


def getFileOfElement(element):

    """
    Returns the name of the file `element` was read from.
    """

    p.debug("retrieving hierarchy of {}".format(element))
    elementHierarchy = iH.Hierarchy.checkAndGetHierarchy(element)
    if not elementHierarchy: # element is not addable
        return None

    strHierarchy = [ item.__class__.__name__ for item in elementHierarchy ]
    if "Viewpoint" in strHierarchy:
        try:
            vpRefIndex = strHierarchy.index("ViewpointReference")
        except Exception as e:
            print("ViewpointReference is not in Hierarchy of Viewpoint",
                    file=sys.stderr)
            raise e
        else:
            viewpointFile = elementHierarchy[vpRefIndex].file
            return viewpointFile

    elif "Markup" in strHierarchy:
        return "markup.bcf"
    elif "Project" in strHierarchy: # it should not come to this point actually
        return "project.bcfp"
    else: # This can only happen if someone wants to change the version file, which is not editable in the plugin
        return None


def getTopicOfElement(element):

    """
    Returns the topic of an element. This is used to generate the right path to
    the file that shall be edited.
    """

    elementHierarchy = iH.Hierarchy.checkAndGetHierarchy(element)
    if not elementHierarchy: # just check for sanity
        return None

    strHierarchy = [ item.__class__.__name__ for item in elementHierarchy ]
    p.debug("hierarchy of {}:\n{}\n".format(
        element.__class__.__name__, elementHierarchy))
    if "Markup" in strHierarchy:
        markupElem = None
        for item in elementHierarchy:
            if isinstance(item, m.Markup):
                markupElem = item
                break
        return markupElem.topic

    return None


def getIdAttrName(elementId):

    idAttrName = ""
    if isinstance(elementId, UUID):
        idAttrName = "Guid"
    elif isinstance(elementId, str):
        idAttrName = "IfcGuid"

    return idAttrName


def getEtElementById(elemId, elemName, etRoot):

    """
    Searches for an element with the attribute `idAttrName` that has the value
    of `listElemId`.
    """

    idAttrName = getIdAttrName(elemId)
    p.debug("searching elementtree for .//{}[@{}='{}']".format(
            elemName, idAttrName, elemId))
    etParent = etRoot.find(".//{}[@{}='{}']".format(elemName,
            idAttrName, str(elemId)))
    return etParent


def searchEtByTag(etRoot, tag):

    p.debug("searching elementtree for .//{} starting at {}".format(
            tag, etRoot.tag))
    result = etRoot.find(".//{}".format(tag))
    p.debug("got {}".format(
            result))
    return result


def getParentElement(element, etRoot):

    """
    Searches `etRoot` for the parent of `element` and returns it if found. If
    the element turns out to be itself a root element of a file (e.g.:
    VisualizationInfo in viewpoint.bcfv) then a NotImplementedError is raised.
    """

    elementHierarchy = element.getHierarchyList()
    strHierarchy = [ elem.xmlName for elem in elementHierarchy ]
    parentName = strHierarchy[1]

    if len(strHierarchy) < 2:
        raise NotImplementedError("Element itself is a root element."\
                "Creating of new files is not supported yet")

    # the topmost element will always be Project
    if strHierarchy[-2] != etRoot.tag:
        print("Root element of hierarchy and root tag of file do not match."\
            " {} != {}".format(strHierarchy[-1], etRoot.tag), file=sys.stderr)

    etParent = None
    listElemId = getUniqueIdOfListElementInHierarchy(element)
    p.debug("got list id {} for {}".format(listElemId,
        element.__class__.__name__))

    # parent can be found easily by tag
    if not listElemId:
        etParent = searchEtByTag(etRoot, parentName)
        # check whether element is a first order child of root
        if not etParent and etRoot.tag == parentName:
            etParent = etRoot

    # the parent is identified by a unique id
    else:
        etListAncestor = getEtElementById(listElemId, parentName, etRoot)
        # check whether the list element `element` is contained in is also its
        # parent
        if etListAncestor.tag == element.containingObject.xmlName:
            # we're done
            etParent = etListAncestor
        else:
            # Assume that nested lists do not exist in the bcf file and search
            # for `element.containingObject` by its name
            etParent = searchEtByTag(etListAncestor, parentName)
            if not etParent:
                raise RuntimeError("An unknown error occured while searching"\
                        "for element {} inside {}".format(element,
                            etListAncestor))

    p.debug("found {} as parent of {}".format(etParent, element.xmlName))
    return etParent


def getInsertionIndex(element, etParent):

    """
    Returns the index at which `element` shall be inserted into `etParent`.
    This index is always the greatest possible one complying with the schema
    file.
    Therefore if already multiple elements with the same tag as `element` exist
    then `element` will be inserted last.
    """

    definedSequence = elementOrder[etParent.tag]
    # order of elements how they are found in the file in etParent
    actualSequence = [ elem.tag for elem in list(etParent) ]
    actualSequenceRev = list(reversed(actualSequence))

    p.debug("writer.{}()\n\tdefined sequence: {}\n\tactual"\
                " sequence: {}".format(getInsertionIndex.__name__,
                    definedSequence, actualSequence))
    p.debug("writer.{}(): element is of type {}".format(
            getInsertionIndex.__name__, type(element)))

    insertionIndex = len(actualSequenceRev)-1
    # element is already contained => insert it after the last occurence
    if element.xmlName in actualSequence:
        insertionIndex = (len(actualSequenceRev) -
                actualSequenceRev.index(element.xmlName))

    # find the first successor in actualSequence according to definedSequence
    # and insert it infront
    else:
        elemIdxInDefinedSequence = definedSequence.index(element.xmlName)
        # element is the last one in definedSequence => insert it as the last
        # element in the actualSequence
        if elemIdxInDefinedSequence == len(definedSequence) - 1:
            insertionIndex = len(actualSequenceRev)
        else:
            for elem in definedSequence[elemIdxInDefinedSequence + 1:]:
                p.debug("writer.getInsertionIndex(): is {} in" \
                        " actualSequence?".format(elem))
                # first successor found. Insert it before it
                if elem in actualSequence:
                    insertionIndex = actualSequence.index(elem)
                    break

    p.debug("writer.{}(): index at which element is inserted {}".format(
            getInsertionIndex.__name__, insertionIndex))
    return insertionIndex


def getEtElementFromFile(rootElem: ET.Element, wantedElement, ignoreNames=[]):

    """
    This function searches `rootElem` for all occurences for
    containingElement.xmlName. This set of elements is then searched for the
    best match. First the strategy of matching on the containing elements is
    tried. If the element is empty then it is tried to match on the attributes.
    For both strategies it holds that the first match is returned. If a match is
    found it is returned as object of type xml.etree.ElementTree.Element. If no
    match is found then `None` is returned.
    """

    # candidates are the set of elements that have the same tag as
    # containingElement
    candidates = rootElem.findall(".//{}".format(wantedElement.xmlName))
    parentEt = wantedElement.getEtElement(
           ET.Element(wantedElement.xmlName, {}))
    parentEtChildren = list(parentEt)
    p.debug("looking for {} element in: \n\t{}\n\twith the"\
            " exceptions of: {}".format(
            ET.tostring(parentEt),
            [ ET.tostring(c) for c in candidates ],
            ignoreNames))
    match = None
    # find the right candidate
    for candidate in candidates:
        if len(parentEtChildren) > 0:
            # check for subelement in the parent whether the equally named
            # subelement in the candidate has the same text, and therefore is equal
            matches = True
            for parentEtChild in parentEtChildren:
                # ignore children that are contained in the ignore list
                if parentEtChild.tag in ignoreNames:
                    continue

                candidateEtChild = candidate.find(
                        ".//{}".format(parentEtChild.tag))
                if candidateEtChild is not None:
                    if candidateEtChild.text != parentEtChild.text:
                        matches = False
                        break
                else:
                    matches = False
            if matches:
                match = candidate
                break

        # if the element does not have any subelements, match onto the
        # attributes.
        elif len(parentEt.attrib.keys()) > 0:
            # Number of attributes in parentEt and candidate have to match
            nrParentEtAttribs = len(parentEt.attrib.keys())
            nrCandidateAttribs = len(candidate.attrib.keys())
            nrIgnoreNames = len(ignoreNames)
            # all to be ignored names have to be considered in the length check
            if (nrParentEtAttribs - nrIgnoreNames) != nrCandidateAttribs:
                continue

            matches = True
            for key in candidate.attrib.keys():
                if key in parentEt.attrib:
                    if parentEt.attrib[key] != candidate.attrib[key]:
                        matches = False
                        break
                else:
                    matches = False
                    break

            if matches:
                match = candidate
                break

        # try matching element on text
        elif parentEt.text != "":
            if parentEt.text == candidate.text:
                match = candidate
                break
        else:
            raise RuntimeError("Could not find any matching element that could"\
                    "be modified")

    return match


def generateViewpointFileName(markup: m.Markup):

    """
    Generates a new viewpoint file name. It will have the name:
        `viewpointX.bcfv`
    where `X` is an arbitrary number. Initially X is set to one and incremented
    until an X is reached that does not yield an existing filename (in
    combination with `base_name`. The first hit is returned.
    """

    filenames = [ vpRef.file for vpRef in markup.viewpoints ]
    base_name = "viewpoint{}.bcfv"

    idx = 1
    name_candidate = base_name.format(idx)
    while name_candidate in filenames:
        idx += 1
        name_candidate = base_name.format(idx)

    return name_candidate


def xmlPrettify(element: ET.Element):

    """
    uses xml.dom.minidom to parse the string output of element and then again
    convert it to a string, but now nicely formatted.
    The formatted string is returned.
    """

    unformatted = ET.tostring(element, encoding="utf8")
    domParsed = MD.parseString(unformatted)
    formatted = domParsed.toprettyxml(encoding="UTF-8", indent="\t").decode("utf-8")
    # remove possible blank lines
    prettyXML = "\n".join([ line for line in formatted.split("\n")
                            if line.strip() ])
    return prettyXML.encode("UTF-8") # just to be sure to use utf8


def getTopicPath(element):

    topic = getTopicOfElement(element)
    if not topic:
        return None

    topicDir = str(topic.xmlId)
    bcfDir = reader.bcfDir
    return os.path.join(bcfDir, topicDir)


def writeXMLFile(xmlroot, filePath):

    """
    Formats `xmlroot` and then writes it to `filePath` (UTF8 encoded)
    """

    xmlPrettyText = xmlPrettify(xmlroot)
    with open(filePath, "wb") as f:
        f.write(xmlPrettyText)


def _addAttribute(element, xmlroot):

    newParent = element.containingObject

    # parent element of the attribute how it should be
    newParentEt = newParent.getEtElement(ET.Element(newParent.xmlName, {}))
    p.debug("=========\nnew parent generated:\n{}\n=========".format(
            ET.tostring(newParentEt)))

    # parent element of the attribute as is in the file, and ignore the new
    # attribute if the element is searched by its attributes
    oldParentEt = getEtElementFromFile(xmlroot,
            newParent, [element.xmlName])

    if oldParentEt is None:
        raise RuntimeWarning("The element {}, parent of the attribute {},"\
            " was not present in the file. Not adding the attribute"\
            " then!".format(newParentEt.tag, element.xmlName))

    # add the value of the new attribute
    oldParentEt.attrib[element.xmlName] = newParentEt.attrib[element.xmlName]

    return xmlroot


def _addElement(element, xmlroot):

    # parent element read from file
    etParent = getParentElement(element, xmlroot)

    # index of the direct predecessor element in the xml file
    insertionIndex = getInsertionIndex(element, etParent)
    newEtElement = element.getEtElement(ET.Element(element.xmlName))
    etParent.insert(insertionIndex, newEtElement)

    return xmlroot


def _createViewpoint(element, topicPath):

    if element.file is None:
        raise RuntimeWarning("The new viewpoint does not have a filename."\
                "Generating a new one!")
        # element.containingObject == Markup
        element.file = generateViewpointFileName(element.containingObject)

    vp = element.viewpoint
    visinfoRootEtElem = ET.Element("", {})
    vp.getEtElement(visinfoRootEtElem)

    p.debug("Writing new viewpoint to"\
                " {}".format(element.file))

    vpFilePath = os.path.join(topicPath, str(element.file))
    writeXMLFile(visinfoRootEtElem, vpFilePath)


def _createMarkup(element, topicPath):

    """
    For the markup `element` create a new topic folder and the markup.bcf file.
    If already viewpoints are referenced then also create the new viewpoint
    files.
    """

    if os.path.exists(topicPath):
        raise RuntimeError("The topic {} does already exist.")

    p.debug("Creating new markup {}".format(topicPath))

    os.mkdir(topicPath)
    markupPath = os.path.join(topicPath, "markup.bcf")
    # just create the markup file
    with open(markupPath, 'w') as markupFile: pass

    markupXMLRoot = ET.Element("Markup", {})
    markupXMLRoot = element.getEtElement(markupXMLRoot)
    writeXMLFile(markupXMLRoot, markupPath)

    for viewpoint in element.viewpoints:
        _createViewpoint(viewpoint, topicPath)


def addElement(element):

    """
    In this context an element can be a simple or complex xml element as well as
    just an attribute of an element that was added to the datamodel and shall
    now be added to the file as well.
    Both additions have the following approach in common:
        - the current file is read into an xml.etree.ElementTree structure.
        - this structure is updated with the new values
        - the old file is overwritten with the updated xml.etree.ElementTree
          structure
    For the addition of attributes it is assumed that the containing element
    already exists in the file. This element is searched for and expanded by the
    new attribute.
    For the addition of elements the parent element is searched for, and in the
    predefined sequence of the parent the right insertion index is looked up,
    since the element cant just be appended, otherwise it would not be schema
    conform anymore.
    """

    # filename in which `element` will be found
    fileName = getFileOfElement(element)
    if not fileName:
        raise ValueError("{} is not applicable to be added to anyone"\
            "file".format(element.xmlName))

    if not (".bcfv" in fileName or ".bcf" in fileName):
        raise NotImplementedError("Writing of project.bcfp or bcf.version"\
                " is not yet supported")

    topicPath = getTopicPath(element)
    if not topicPath:
        raise RuntimeError("Element {} could not be associated to any topic."\
            "This may be the case if properties in project.bcfp should be"\
            "modified, which is currently not implemented!".format(str(element)))

    p.debug("adding new element {}".format(element))
    # adds a complete new topic folder to the zip file
    if isinstance(element, m.Markup):
        _createMarkup(element, topicPath)
        return

    filePath = os.path.join(topicPath, fileName)
    xmltree = ET.parse(filePath)
    xmlroot = xmltree.getroot()

    # different handling for attributes and elements
    if isinstance(element, p.Attribute):
        xmlroot = _addAttribute(element, xmlroot)

    else:
        xmlroot = _addElement(element, xmlroot)

    # generate viewpoint.bcfv file for added viewpoint
    if (isinstance(element, m.ViewpointReference) and
            element.viewpoint is not None and
            element.viewpoint.state == iS.State.States.ADDED):

        _createViewpoint(element, topicPath)

    writeXMLFile(xmlroot, filePath)


def deleteXMLIdentifiableElement(element, xmlroot):

    """
    Deletes an element that can be identified by an id.
    Returns the updated xmlroot
    """

    elemId = element.xmlId
    etElem = getEtElementById(elemId, element.xmlName, xmlroot)
    p.debug("{} corresponds to ETElement {}".format(element, etElem))

    etParent = getParentElement(element, xmlroot)
    p.debug("parent of {} is {}".format(etElem, etParent))

    etParent.remove(etElem)
    p.debug("removed {} from {}".format(etElem, etParent))

    return xmlroot


def deleteElement(element):

    """
    Viewpoint files are only deleted if they are flagged with the state DELETED
    and their accompanying viewpoint references are also deleted.
    """

    p.debug("Deleting element {}".format(element))
    # filename in which `element` will be found
    fileName = getFileOfElement(element)
    if not fileName:
        raise ValueError("For {} no file can be found to delete from"\
            "".format(element.__class__.__name__))

    # path of the topic `element` is contained in
    topicPath = getTopicPath(element)
    # filepath of the file `element` is contained in
    filePath = os.path.join(topicPath, fileName)
    # parsed version of the file
    xmlfile = ET.parse(filePath)
    xmlroot = xmlfile.getroot()

    # if identifiable then search for the guid using xmlpath.
    if issubclass(type(element), iI.XMLIdentifiable):
        p.debug("{} inherits from XMLIdentifiable -> deleting by"\
                " Id".format(element))
        deleteXMLIdentifiableElement(element, xmlroot)

        if isinstance(element, m.ViewpointReference):
            if element.viewpoint.state == iS.State.States.DELETED:
                vpElem = element.viewpoint
                p.debug("with viewpoint reference also the viewpoint {}"\
                        " gets deleted".format(vpElem))

                vpFile = getFileOfElement(vpElem)
                if not vpFile:
                    raise ValueError("No file could be found for element {}"\
                        "\nSo the element won't be deleted.".format(vpElem))

                vpFilePath = os.path.join(topicPath, str(vpFile))
                os.remove(vpFilePath)
                p.debug("Removed file {}".format(vpFilePath))

    # attributes have to be deleted from the attrib dictionary
    elif isinstance(element, p.Attribute):
        parentElem = element.containingObject
        parentEtElem = getEtElementFromFile(xmlroot, parentElem, [])

        p.debug("Deleting {} from {}".format(element, parentEtElem))
        p.debug("Available attributes in {} are: {}".format(parentEtElem,
            list(parentEtElem.keys())))
        del parentEtElem.attrib[element.xmlName]

    # otherwise employ getEtElementFromFile to get the right element
    else:
        p.debug("{} does not inherit from XMLIdentifiable".format(element))

        fileEtElement = getEtElementFromFile(xmlroot, element, [])
        parentEtElement = getParentElement(element, xmlroot)
        #parentEtElement = getEtElementFromFile(xmlroot,
                #element.containingObject, [])

        p.debug("Element {}\ncorresponds to {}\nin file, and has parent"\
                " {}".format(element, fileEtElement, parentEtElement))
        parentEtElement.remove(fileEtElement)

    writeXMLFile(xmlroot, filePath)



def modifyElement(element, previousValue):

    """
    Modifies the xml node corresponding to `element`. `element` has to be of
    type Attribute or SimpleElement. Other elements (e.g. comments, viewpoints,
    etc.) must not be of state modified since the modification is inside an
    child-member.
    """

    if not (issubclass(type(element), p.SimpleElement) or
            issubclass(type(element), p.Attribute)):
        raise ValueError("Element is not an attribute or simple element. Only"\
                " these two types can be updated. Actual type of element:"\
                " {}".format(type(element)))

    p.debug("Modifying element {}".format(element))
    # filename in which `element` will be found
    fileName = getFileOfElement(element)
    if not fileName:
        raise ValueError("For {} no file can be found that contains it."\
            "file".format(element))

    # path of the topic `element` is contained in
    topicPath = getTopicPath(element)
    # filepath of the file `element` is contained in
    filePath = os.path.join(topicPath, fileName)
    # parsed version of the file
    xmlfile = ET.parse(filePath)
    xmlroot = xmlfile.getroot()

    # set element to old state to get more reliable matching
    newValue = element.value
    element.value = previousValue
    if issubclass(type(element), p.SimpleElement):
        parentElem = element.containingObject
        etElem = getEtElementFromFile(xmlroot, element, [])
        etElem.text = newValue

    elif issubclass(type(element), p.Attribute):
        parentElem = element.containingObject
        parentEtElem = getEtElementFromFile(xmlroot, parentElem, [])
        parentEtElem.attrib[element.xmlName] = newValue

    writeXMLFile(xmlroot, filePath)


def addProjectUpdate(project: p.Project, element, prevVal):

    global projectUpdates
    if element.state != iI.State.States.ORIGINAL:
        projectUpdates.append((project, element, prevVal))
    else:
        raise ValueError("Element is in its original state. Cannot be added as"\
                " update")


def writeHandlerErrMsg(msg):
    p.debug(msg)
    print(str(err), file=sys.stderr)
    print(msg, file=sys.stderr)


def handleAddElement(element, oldVal):

    """
    Calls `addElement` on `element` and handles the errors that might be raised.
    Every error is printed to dbg output and stderr. If an error is catched then
    `False` is returned to indicate that the update was not successful. In case
    of a successful update `True` is returned.
    """

    try:
        addElement(element)
    except (RuntimeWarning, ValueError, NotImplementedError) as err:
        msg = ("Element {} could not be added. Reverting to previous" \
            " state".format(element))
        if isinstance(err, NotImplementedError):
            msg = ("Adding {} is not implemented. Reverting to previous"\
                    " state".format(element))
        writeHandlerErrMsg(msg)
        return False
    except Exception as exc:
        msg = "An unknown excption occured while adding"\
            " {}".format(element)
        writeHandlerErrMsg(msg)
        return False
    else:
        return True


def handleDeleteElement(element, oldVal):

    """
    Calls `deleteElement` on `element` and handles the errors that might be raised.
    Every error is printed to dbg output and stderr. If an error is catched then
    `False` is returned to indicate that the update was not successful. In case
    of a successful update `True` is returned.
    """

    try:
        deleteElement(element)
    except ValueError as err:
        msg = ("Element {} could not be deleted. Reverting to previous "\
                "state".format(element))
        writeHandlerErrMsg(msg)
        return False
    except Exception as exc:
        msg = "An unknown excption occured while adding"\
            " {}".format(element)
        writeHandlerErrMsg(msg)
        return False
    else:
        return True


def handleModifyElement(element, prevVal):

    """
    Calls `modifyElement` on `element` and handles the errors that might be raised.
    Every error is printed to dbg output and stderr. If an error is catched then
    `False` is returned to indicate that the update was not successful. In case
    of a successful update `True` is returned.
    """

    try:
        modifyElement(element, prevVal)
    except ValueError as err:
        msg = ("Element {} could not be modified. Reverting to previous "\
                "state".format(element))
        writeHandlerErrMsg(msg)
        return False
    except Exception as exc:
        msg = "An unknown excption occured while adding"\
            " {}".format(element)
        writeHandlerErrMsg(msg)
        return False
    else:
        return True


def updateProjectSnapshots(newUpdates):

    """
    Adds the project copies to `projectSnapshots`, but preserves its length of
    `SNAPSHOT_CNT` elements.
    If the list `newUpdates` is longer than five elements all elements of
    `projectSnapshots` are replaced with the last `SNAPSHOT_CNT` elements of
    `newUpdates`. Otherwise the contents of `projectSnapshots` are shifted by
    `len(newUpdates)` (i.e.: the `len(newUpdates)` oldest elements are deleted)
    and the contents of newUpdates are pasted.
    """

    for newUpdate in newUpdates:
        projectSnapshots.append(newUpdate)


def processProjectUpdates():

    """
    Process to process all updates stored in `projectUpdates`. The updates are
    processed in chronological order in a loop. If one update fails the
    processing is stopped. Every update that was processed in a successful
    manner is added to the `processedUpdates`.

    If all updates were processed successfully then `None` is returned.
    Otherwise the failed update will be returned.
    """

    global projectUpdates

    # list of all updates that were successfully processed
    processedUpdates = list()
    # holds the update that failed to be able to revert back
    errorenousUpdate = None
    for update in projectUpdates:
        element = update[1]
        oldVal = update[2]
        updateType = element.state
        if  updateType == iS.State.States.ADDED:
            if handleAddElement(element):
                processedUpdates.append(update, oldVal)
            else:
                errorenousUpdate = update
                break
        if updateType == iS.State.States.DELETED:
            if handleDeleteElement(element):
                processedUpdates.append(update, oldVal)
            else:
                errorenousUpdate = update
                break
        if updateType == iS.State.States.MODIFIED:
            if handleModifyElement(element, oldVal):
                pass
            else:
                errorenousUpdate = update
                break

    updateProjectSnapshots(processedUpdates)
    if errorenousUpdate is not None:
        return errorenousUpdate
    else:
        return None


def addUpdate(projectCpy, element, prevVal):

    """
    Adds the supplied parameters, packed into a tuple, to list
    `projectUpdates`. Thereby it is assumed that `projectCpy` is a deep copy of
    the current working `project` object and `element` is a reference into this
    project copy. `prevVal` is the previous value of the element, it is only
    set to `!= None` if `element.state == MODIFIED`.
    """

    projectUpdates.append((projectCpy, element, prevVal))


################## DEPRECATED ##################
def compileChanges(project: p.Project):

    """
    This function crawls through the complete object structure below project and
    looks for objects whose state is different from `iS.State.States.ORIGINAL`.
    Elements that are flagged with `iS.State.States.ADDED` are put into the list
    `addedObjects`.
    Elements that are flagged with `iS.State.States.DELETED` are put into the
    list `deletedObjects`.
    Elements that are flagges with `iS.State.States.MODIFIED` are put into the
    list `modifiedObjects`.
    These lists are then, in a subsequent step, processed and written to file.
    """

    stateList = project.getStateList()
    for item in stateList:
        if item[0] == iS.State.States.ADDED:
            addedObjects.append(item[1])
        elif item[0] == iS.State.States.MODIFIED:
            modifiedObjects.append(item[1])
        elif item[0] == iS.State.States.DELETED:
            deletedObjects.append(item[1])
        else: # Last option would be original state, which should not be contained in the list anyways
            pass
################################################


if __name__ == "__main__":
    argFile = "test_data/Issues_BIMcollab_Example.bcf"
    if len(sys.argv) >= 2:
        argFile = sys.argv[1]
    project = reader.readBcfFile(argFile)
    markup = project.topicList[0]
    topic = project.topicList[0].topic
    """
    hFiles = project.topicList[0].header.files
    addElement(project.topicList[0].viewpoints[0])
    addElement(project.topicList[0].comments[0])
    hFiles[1].ifcProjectId = "abcdefg"
    hFiles[1].ifcSpatialStructureElement = "abcdefg"
    addElement(hFiles[1]._ifcProjectId)
    addElement(hFiles[1]._ifcSpatialStructureElement)
    bimSnippet = topic.bimSnippet
    print(topic.bimSnippet)
    addElement(bimSnippet._external)

    docRef = topic.refs[0]
    docRef.external = True
    docRef.guid = "98b5802c-4ca0-4032-9128-b9c606955c4f"
    print(docRef)
    addElement(docRef._external)
    addElement(docRef._guid)
    """

    print(markup.viewpoints)
    newVp = c.deepcopy(markup.viewpoints[0])
    newVp.file = u.Uri("viewpoint2.bcfv")
    newVp.index = 2
    newVp.state = iS.State.States.ADDED
    newVp.viewpoint.state = iS.State.States.ADDED
    markup.viewpoints.append(newVp)
    addElement(newVp)
    stateList = project.getStateList()
    print(stateList)
