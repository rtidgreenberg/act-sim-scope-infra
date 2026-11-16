# QoS Auto-configuration of Routing Service Entities (ROUTING-1309)

# Overview

This feature introduces a new configuration option for **Routing Service** that allows it to automatically configure a subset of QoS settings in a route’s DataWriter, DataReader, and a session’s Publisher and Subscriber. The goal of this feature is to maximize the compatibility of Routing Service with discovered entities. Therefore, the QoS policies that may be modified are those that have relevance in the endpoint matching process. Some of these QoS adjustments are derived from the QoS values of discovered entities, while others are set to universally compatible values that improve interoperability.

The option can be configured by using the new \<*auto\_configure\_qos*\> tag present in:

* **Topic Routes and Auto Topic Routes**: the setting can be used with Inputs and/or Outputs of Topic Routes and Auto Topic Routes. It will apply to the Data Reader and Data Writer QoS of the associated DDS entities.

|     \<routing\_service name\="MyConfigWithAutoTopicRouteAutoQos"\>        \<domain\_route name\="DomainRoute"\>            \<participant name\="domain0"\>                ...            \</participant\>            \<participant name\="domain1"\>                ...            \</participant\>            \<session name\="Session"\>                \<auto\_topic\_route\>                    \<input participant\="domain0"\>                        \<auto\_configure\_qos\>true\</auto\_configure\_qos\>                        \<creation\_mode\>ON\_DOMAIN\_MATCH\</creation\_mode\>                                               \<allow\_topic\_name\_filter\>MyTopic\*\</allow\_topic\_name\_filter\>                        \<datareader\_qos base\_name\="MyBaseLibrary::MyBaseProfile"/\>                    \</input\>                    \<output participant\="domain1"\>                        \<auto\_configure\_qos\>true\</auto\_configure\_qos\>                        \<creation\_mode\>ON\_DOMAIN\_MATCH\</creation\_mode\>                        \<allow\_topic\_name\_filter\>MyTopic\*\</allow\_topic\_name\_filter\>                        \<datawriter\_qos base\_name\="MyBaseLibrary::MyBaseProfile"/\>                    \</output\>                \</auto\_topic\_route\>            \</session\>        \</domain\_route\>    \</routing\_service\> |
| :---- |

* **Sessions**: the tag \<*auto\_configure\_publisher\_qos*\> can be used to enable the setting for a Session’s DDS Publisher. Similarly, the \<*auto\_configure\_subscriber\_qos*\> tag can be used to enable the setting for a Session’s DDS Subscriber.

|     \<routing\_service name\="MyConfigWithAutoPubSubQos"\>        \<domain\_route name\="DomainRoute"\>            \<participant name\="domain0"\>                ...            \</participant\>            \<participant name\="domain1"\>                ...            \</participant\>            \<session name\="Session"\>                \<auto\_configure\_publisher\_qos\>true\</auto\_configure\_publisher\_qos\>                \<auto\_configure\_subscriber\_qos\>true\</auto\_configure\_subscriber\_qos\>                \<subscriber\_qos base\_name\="MyBaseLibrary::MyBaseProfile"/\>                \<publisher\_qos base\_name\="MyBaseLibrary::MyBaseProfile"/\>                \<auto\_topic\_route\>                    \<input participant\="domain0"\>                        \<creation\_mode\>ON\_DOMAIN\_MATCH\</creation\_mode\>                                               ...                    \</input\>                    \<output participant\="domain1"\>                        \<creation\_mode\>ON\_DOMAIN\_MATCH\</creation\_mode\>                        ...                    \</output\>                \</auto\_topic\_route\>            \</session\>        \</domain\_route\>    \</routing\_service\> |
| :---- |

Auto-configure QoS values are applied only if the XML QoS values match the default QoS profile values. This ensures that user-specified XML QoS values take precedence over auto-configured values. The QoS values are applied in the following order of priority:

1. *XML-defined QoS values*: these are explicitly defined by the user in the XML configuration. If they differ from the default QoS values, they are applied directly.  
2. *Auto-configure QoS values*: If the XML QoS values match the default QoS values, the system applies auto-configured QoS values based on the QoSs of the discovered entities or universally compatible values.  
3. *Default QoS values*: If neither XML QoS values nor auto-configure QoS values are applied, the default QoS values are used as a fallback.

| Note: the \<auto\_configure\_qos\> setting requires the Route’s Inputs/Outputs creation mode to be set to ON\_DOMAIN\_MATCH or ON\_DOMAIN\_AND\_ROUTE\_MATCH |
| :---- |

#  Automatically Configured QoS Policies and Settings

The following sections introduce the QoS Policies that are affected and how when the \<*auto\_configure\_qos*\> option is set to TRUE.

If a QoS policy is not listed here, it means it’s not auto-configured or modified regardless of the setting being on.

## Durability QoS Policy

| Set based on discovered values |
| :---- |

The only Durability setting set automatically from discovery is the **Durability Kind**. 

For both DataReaders and DataWriters, if the Durability Kind specified in XML is equal to the default value, Routing Service will auto-configure it using the value discovered from the corresponding remote entity.

The other settings (*direct\_communication*, *writer\_depth*, *storage\_settings*) are writer-specific or cannot be used to automatically configure the counterpart entity.

## Reliability QoS Policy

| Set based on discovered value DataReader Set to RELIABLE for DataWriter |
| :---- |

The only Reliability setting set automatically from discovery is the **Reliability Kind**.

For Data Readers, if the Reliability Kind value coming from the XML settings is equal to the default value, Routing Service will auto-configure it using the value discovered from the corresponding remote entity.

The default Reliability Kind is **RELIABLE**, which already provides maximum compatibility. Therefore, no auto-configuration is applied to DataWriters.

The rest of the settings (*max\_blocking\_time*, *acknowledgment\_kind* and *instance\_state\_consistency\_kind*) are not configured automatically.

### Potential Change (K Release)

JIRA: 

* [https\://1rti.atlassian.net/browse/ROUTING-1062](https://1rti.atlassian.net/browse/ROUTING-1062)  
* [https\://1rti.atlassian.net/browse/CORE-13337](https://1rti.atlassian.net/browse/CORE-13337)

Customer: Swiss Timing

The proposal for instance state propagation is described [here](https://docs.google.com/document/d/161wFKV23DjahQvu18VrLOIOVlrdKCZkdhF9FUqdWkE4/edit?tab=t.0#heading=h.a6bgg7bgw3w1)

## Ownership QoS Policy

| Set based on discovered values |
| :---- |

For both DataReaders and DataWriters, if the **Ownership Kind** specified in XML is equal to the default value, Routing Service will auto-configure it using the value discovered from the corresponding remote entity.

### Potential Change (K Release)

In addition to auto-configuring the Ownership Kind, we propose adding support for end-to-end propagation of ownership strength. Users will be able to explicitly control this behavior through a new route-level configuration: **\<propagate\_ownership\_strength\>**.

The proposed approach is for Routing Service to create one DataWriter per ownership strength. If the original writer associated with a given strength loses liveliness, Routing Service will delete the corresponding DataWriter.

This design assumes that the number of distinct ownership strengths is small (typically 2–3), limiting the number of writers that need to be created.

As an optimization, Routing Service could create these writers lazily, only when a sample is received with a new ownership strength. This avoids creating writers prematurely—for example, in cases where a lower-strength writer is discovered but never actually contributes samples because it is overridden by higher-strength writers.

**Note:** Unlike partition propagation, propagating ownership strength as inline QoS in the samples is not feasible, as there is no reliable mechanism to detect when the DataWriter associated with a given strength is no longer present. Additionally, \<propagate\_ownership\_strength\> does not require \<auto\_configure\_qos\> to be enabled.

## DestinationOrder QoS Policy

| Set based on discovered values |
| :---- |

The Destination Order policy is set automatically from discovery if the value coming from XML is equal to the default. 

Both the **Kind** and **Scope** fields are set with the value coming from Discovery for both Data Readers and Data Writers.

The scope field of the DestinationOrder QoS Policy does not directly affect the matching between a DataWriter and a DataReader. However, it influences how the ordering of samples is enforced once the entities are matched. The scope determines whether the ordering is applied per instance or across all instances of a topic.

### Potential Change (K Release)

JIRA: [https\://1rti.atlassian.net/browse/ROUTING-937](https://1rti.atlassian.net/browse/ROUTING-937)

Customer: Swiss Timing

Supporting doc:

* [Group Order Access and Coherent Set](https://docs.google.com/presentation/d/18-t5jXUTOblWIVn9CGhdAGClm9Jt7nZ3/edit?slide=id.p1#slide=id.p1)

A potential enhancement for the K release is to support propagation of group order and coherent sets through Routing Service. This would require several changes:

* Modify how Routing Service reads samples to use begin\_access() / end\_access()  
* Define behavior for incomplete coherent sets  
* Modify how Routing Service writes samples to support starting and ending coherent sets  
* Handle scenarios where the same coherent set is propagated through multiple Routing Service instances

## DataRepresentation QoS Policy

| Configured automatically |
| :---- |

For Data Readers, if the setting coming from XML is equal to the default , Routing Service will auto-configure it using the value discovered from the corresponding remote entity.

For Data Writers, if the setting from XML is default, then it’s set to XCDR2 when the value discovered from the corresponding remote entity includes XCDR2 (it could include both). Otherwise, it’s set to XCDR.

The *compression\_settings* field is not set automatically. For a DataReader, the default value is MASK\_ALL (accepts all compression algorithms).

## LatencyBudget QoS Policy

| Set to INFINITE for DataReaders |
| :---- |

The LatencyBudget QoS is not configured using the settings coming from discovery. For Data Writers, nothing is done.

However, when the value coming from XML equals the default value and \<*auto\_configure\_qos*\> is TRUE the LatencyBudget QoS setting for Data Readers is set to **INFINITE**.

## Partition QoS Policy

| Set to “\*”\|’’ |
| :---- |

This setting applies to Sessions (Publishers and Subscribers).

When the Partition QoS setting coming from XML is equal to the default value, the Partition QoS of the Session’s Publisher and/or Subscriber QoS settings is set to ensure maximum compatibility across different configurations. It includes setting a wildcard partition ("\*") to match any partition and an empty partition ("") to allow for default behavior.

### Potential Change (K Release)

JIRA: [https\://1rti.atlassian.net/browse/ROUTING-672](https://1rti.atlassian.net/browse/ROUTING-672)

Customers: Komatsu, EADS

As part of the K release, we propose modifying the existing behavior when auto QoS configuration is enabled:

The Subscriber QoS will continue to include both:

* *The wildcard partition ("*"), and  
* The empty partition ("")

The Publisher QoS, however, will be dynamically updated to include the union of all discovered partitions from the input (i.e., an OR-combination of partitions).

Additionally, users will be able to explicitly control end-to-end partition propagation through a new route-level configuration: \<publish\_with\_original\_partition\> (see [Partition Propagation](#partition-propagation)).

#### Partition Propagation {#partition-propagation}

The default “universal compatibility” approach described above is not sufficient to meet all use cases and must be complemented with partition propagation across the routing path. 

To address this, we introduce a new option in \<topic\_route\> or \<auto\_topic\_route\>. When \<publish\_with\_original\_partition\> is enabled, the Routing Service (RS) attaches the original writer partition as inline QoS on each sample, enabling behavior analogous to \<publish\_with\_original\_info\> and \<publish\_with\_original\_timestamp\>. 

Samples are then queued with their original partition, and filtering is performed on the writer side rather than using CFT.

**Note:** Setting \<publish\_with\_original\_partition\> does not require \<auto\_configure\_qos\> to be enabled.
